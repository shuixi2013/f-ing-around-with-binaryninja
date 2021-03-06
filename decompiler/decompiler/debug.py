from __future__ import annotations
from typing import Dict, List

from binaryninja import (BinaryView, BranchType, FlowGraph, FlowGraphNode,
                         FlowGraphReport, ReportCollection, show_graph_report,
                         BasicBlockEdge, MediumLevelILBasicBlock, Settings)

from . import mlil_ast
from .nodes import MediumLevelILAstNode


def generate_graph(
    view: BinaryView,
    region: MediumLevelILAstNode,
    collection: ReportCollection = None,
    title: str = ''
):
    if not Settings().get_bool('linearmlil.debug'):
        return

    graph = FlowGraph()

    def add_children(node: MediumLevelILAstNode) -> FlowGraphNode:
        node_node = FlowGraphNode(graph)
        graph.append(node_node)

        node_line = node.type

        if node.type == 'block':
            node_line += f': {node.block}'
        if node.type == 'break':
            node_line += f': {node.start}'
        elif node.type in ('seq', 'case'):
            node_line += f': {node.start}'
            for child in node.nodes:
                child_node = add_children(child)
                node_node.add_outgoing_edge(
                    BranchType.UnconditionalBranch,
                    child_node
                )
        elif node.type == 'cond':
            node_line += f': {node.condition}'
            child = add_children(node[True])
            node_node.add_outgoing_edge(
                BranchType.TrueBranch,
                child
            )
            if node[False] is not None:
                child = add_children(node[False])
                node_node.add_outgoing_edge(
                    BranchType.FalseBranch,
                    child
                )
        elif node.type == 'switch':
            for child in node.cases:
                child_node = add_children(child)
                node_node.add_outgoing_edge(
                    BranchType.UnconditionalBranch,
                    child_node
                )
        elif node.type == 'loop':
            node_line += f': {node.loop_type} {node.condition}'
            child_node = add_children(node.body)
            node_node.add_outgoing_edge(
                BranchType.UnconditionalBranch,
                child_node
            )

        node_node.lines = [node_line]

        return node_node

    # iterate over regions and create nodes for them
    # in the AST
    add_children(region)

    if collection is not None:
        if not title:
            title = f'    {region.type}: {region.start}'
        report = FlowGraphReport(title, graph, view)
        collection.append(report)
    else:
        show_graph_report('Current AST', graph)


def graph_slice(
    view: BinaryView,
    ns: MediumLevelILBasicBlock,
    ne: MediumLevelILBasicBlock,
    slice: List[List[BasicBlockEdge]],
    collection: ReportCollection,
    title: str = '',
):
    if not Settings().get_bool('linearmlil.debug'):
        return

    graph = FlowGraph()

    ns_node = FlowGraphNode(graph)
    ns_node.lines = [f'Start: {ns.start}']

    ne_node = FlowGraphNode(graph)
    ne_node.lines = [f'End: {ne.start}']

    nodes = {ns.start: ns_node, ne.start: ne_node}

    graph.append(ns_node)
    graph.append(ne_node)

    for path in slice:
        for edge in path:
            source = edge.source
            if source.start in nodes:
                source_node = nodes[source.start]
            else:
                source_node = FlowGraphNode(graph)
                source_node.lines = [f'Block: {source.start}']
                nodes[source.start] = source_node
                graph.append(source_node)

            target = edge.target

            if target.start in nodes:
                target_node = nodes[target.start]
            else:
                target_node = FlowGraphNode(graph)
                target_node.lines = [f'Block: {target.start}']
                nodes[target.start] = target_node
                graph.append(target_node)

            if next(
                (
                    e for e in source_node.outgoing_edges
                    if e.target == target_node
                ),
                None
            ):
                continue

            source_node.add_outgoing_edge(
                edge.type,
                target_node
            )

    if collection is not None:
        if not title:
            title = f'Slice: {ns}->{ne}'
        report = FlowGraphReport(title, graph, view)
        collection.append(report)
    else:
        show_graph_report('Graph Slice', graph)
