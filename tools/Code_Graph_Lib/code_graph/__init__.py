"""The project's code graph, built the same way as its ALM context graph.

CodeGraphContext parses the repository into a throwaway LadybugDB store; its bundle
export is then mapped onto `:CodeNode` nodes with namespaced uids and written by
`connection_sources.graph`'s own loader into the project's Neo4j, next to the ALM
graph and separated from it by marker label and uid prefix.
"""
