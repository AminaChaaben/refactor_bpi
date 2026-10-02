# Installs everything bpi_refactor needs, via scoop. Run in PowerShell from this folder.
# Python must be >=3.11,<3.14 (kuzu 0.11.3 has no cp314 wheels on Windows) -> 3.13.

scoop bucket add versions
scoop bucket add extras
scoop bucket add java

scoop install git
scoop install versions/python313   # interpreter for code-graph (NOT 3.14)
scoop install uv                   # resolves/creates the venv from pyproject + uv.lock

# Neo4j: the detectors query a Neo4j graph. Either install it locally (needs Java 17+/21):
scoop install java/temurin21-jdk
scoop install neo4j
# ...or run it with Docker instead and skip the two lines above:
#   scoop install docker  (or Docker Desktop), then
#   docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5

# Python dependencies (code-graph, connection-sources, codegraphcontext 0.6.13, kuzu 0.11.3, tree-sitter, neo4j, ...)
Push-Location tools\Code_Graph_Lib
uv sync --python 3.13
Pop-Location

Write-Host "Then: copy tools\Connection_Sources_Lib\.env.example to .env and set the Neo4j URI/user/password."
Write-Host "Check: uv run --project tools\Code_Graph_Lib code-graph status"
