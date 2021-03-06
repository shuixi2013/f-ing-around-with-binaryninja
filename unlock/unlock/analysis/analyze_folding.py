from binaryninja import (
    log_debug,
    LowLevelILOperation,
    Variable,
    VariableSourceType,
    SSAVariable,
    SSARegister,
    MediumLevelILInstruction,
    LowLevelILInstruction,
    MediumLevelILOperation,
    ILRegister,
    RegisterValueType,
)

from ..logging import log_debug


def analyze_constant_folding(self, expr):
    log_debug("analyze_constant_folding")

    if isinstance(expr, MediumLevelILInstruction):
        dependents, patch_value, patch_address = analyze_constant_folding_mlil(
            self, expr
        )
    elif isinstance(expr, LowLevelILInstruction):
        dependents, patch_value, patch_address = analyze_constant_folding_llil(
            self, expr
        )

    if None in (dependents, patch_value, patch_address):
        log_debug("got None back, returning")
        return

    if self.view.get_instruction_length(patch_address.address) < len(patch_value):
        log_debug(f"{patch_address.address:x} is too few bytes to patch")
        return False

    # First, convert to NOPs and *then* write the patch
    self.convert_to_nop(patch_address.address)

    log_debug(f"Writing patch at {patch_address.address:x}")
    self.view.write(patch_address.address, patch_value)

    log_debug("NOPPING THE SHIT OUT OF THIS THING")
    # Nop all of the previous assignments
    for addr in dependents:
        log_debug(f"nopping {addr:x}")
        self.convert_to_nop(addr)
    log_debug("DONE WITH ALL THAT NOPPING")

    # place the last address on the queue, to fold
    # all the NOPs and GOTOs
    if dependents:
        self.queue_prev_block(self.function.get_low_level_il_at(dependents[-1]).mmlil)
        return True
    else:
        return


def analyze_constant_folding_llil(self, expr: LowLevelILInstruction):
    log_debug("analyze_constant_folding_llil")

    llil = expr.function.non_ssa_form

    reg_value = expr.value

    log_debug(f"folding {expr.src} into {reg_value.value:x}")

    if expr.operation == LowLevelILOperation.LLIL_REG_SSA:
        reg_ssa = expr.src
        reg_name = reg_ssa.reg.name
        reg_index = reg_ssa.reg.index
    elif expr.operation == LowLevelILOperation.LLIL_REG_SSA_PARTIAL:
        reg_ssa = expr.full_reg
        partial_reg_index = expr.src.index
        reg_index = reg_ssa.reg.index
        reg_name = expr.src.name
    else:
        return

    reg_def = llil[llil.get_ssa_reg_definition(reg_ssa)]

    log_debug(f"register defined at {reg_def.address:x}")

    dependent_regs = []

    next_il = reg_def

    while next_il:
        log_debug(f"{next_il}: {next_il.src.prefix_operands}")
        reg = next(
            (
                o
                for o in next_il.ssa_form.src.prefix_operands
                if isinstance(o, ILRegister) and o.index == partial_reg_index
            ),
            None,
        )
        ssa = next(
            (
                o
                for o in next_il.ssa_form.src.prefix_operands
                if isinstance(o, SSARegister)
                and o.reg.index == reg_index
                and len(llil.get_ssa_reg_uses(o)) == 1
            ),
            None,
        )

        if ssa is not None and reg is None:
            next_il = llil[llil.get_ssa_reg_definition(ssa)]
            dependent_regs.append(next_il.address)
        elif ssa is not None and reg is not None:
            next_il = llil[llil.get_ssa_reg_definition(ssa)]
            if next_il.operation == LowLevelILOperation.LLIL_SET_REG_SSA_PARTIAL:
                dependent_regs.append(next_il.address)
            else:
                next_il = None
        else:
            next_il = None

    # Convert the final one into the assignment
    patch_value = self.view.arch.assemble(
        f"mov {reg_name}, 0x{reg_value.value:x}", reg_def.address
    )

    return dependent_regs, patch_value, reg_def


def analyze_constant_folding_mlil(self, expr: MediumLevelILInstruction):
    log_debug("analyze_constant_folding_mlil")
    mlil = expr.function

    if expr.src.storage > 0x7FFFFFFF:
        log_debug("this is a temp var")
        return

    var_value = mlil[expr.instr_index].src.value

    log_debug(f"folding {expr.src} into {var_value.value:x}")

    var_ssa = expr.ssa_form.src

    var_def = mlil[mlil.get_ssa_var_definition(var_ssa)]

    log_debug(f"variable defined at {var_def.address:x}")

    next_il = var_def

    dependents = [next_il]

    while next_il:
        log_debug(f"{next_il}: {next_il.src.prefix_operands}")
        for operand in next_il.ssa_form.src.prefix_operands:
            if isinstance(operand, SSAVariable) and operand.var == var_ssa.var:
                next_il = mlil[mlil.get_ssa_var_definition(operand)]
                log_debug(f"Adding {next_il} to list")
                dependents.append(next_il)
                break
        else:
            next_il = None

    if dependents:
        log_debug(f"{dependents!r}")
        patch_var = dependents.pop()
        log_debug(f"{patch_var}")

        if patch_var.dest.source_type == VariableSourceType.StackVariableSourceType:
            # Convert the final one into the assignment
            patch_string = f'{"push" if patch_var.llil.dest.operation == LowLevelILOperation.LLIL_SUB else "pop"} 0x{var_value.value:x}'
            log_debug(f"{patch_string} at {patch_var.address:x}")
            patch_value = self.view.arch.assemble(patch_string, patch_var.address)
        elif patch_var.dest.name:
            patch_string = f"mov {patch_var.dest.name}, 0x{var_value.value:x}"
            log_debug(patch_string)
            patch_value = self.view.arch.assemble(patch_string, patch_var.address)
        else:
            log_debug("returning None")
            return [], None, None
    else:
        log_debug("returning None")
        return [], None, None

    return ([i.address for i in dependents] + [expr.address], patch_value, patch_var)


def analyze_goto_folding(self, expr: MediumLevelILInstruction):
    log_debug("analyze_goto_folding")
    llil = expr.function.llil

    llil_jump = expr.llil.non_ssa_form
    log_debug(f"llil_jump = {llil_jump}")

    if llil_jump is None:
        log_debug("We don't have a corresponding LLIL instr?!")
        return False

    final_target = llil[llil_jump.dest]
    log_debug(f"final_target = {final_target}")

    if self.phase < 3:
        jump_ops = (LowLevelILOperation.LLIL_GOTO,)
    else:
        jump_ops = (LowLevelILOperation.LLIL_GOTO, LowLevelILOperation.LLIL_JUMP_TO)

    while final_target.operation in jump_ops:
        if (
            final_target.operation == LowLevelILOperation.LLIL_JUMP_TO
            and final_target.dest.value.type == RegisterValueType.ConstantPointerValue
        ):
            final_target = self.function.get_low_level_il_at(
                final_target.dest.value.value
            )
        else:
            final_target = llil[final_target.dest]
        log_debug(f"final_target = {final_target.address:x}")

    if llil_jump.dest == final_target.instr_index:
        return final_target.mmlil.instr_index

    patch_value = self.view.arch.assemble(
        f"jmp 0x{final_target.address:x}", expr.address
    )

    if self.view.get_instruction_length(expr.address) < len(patch_value):
        log_debug(f"{expr.address:x} is too small for patch")
        return

    self.view.write(expr.address, patch_value)

    self.target_queue.put(final_target.address)

    return False
