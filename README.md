# RF Range-Test MVP

Distributed RF range-test orchestration MVP with:

- a FastAPI control plane
- polling Python host agents
- session-scoped artifact storage
- OpenOCD-compatible flash/verify orchestration
- capture coordination across TX/RX hosts
- raw-log preservation, parsing, merge, and HTML report generation

## Quick start

1. Create a virtual environment and install the package:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

2. Start the server:

```bash
range-test-server
```

3. Start an agent:

```bash
range-test-agent run
```

4. Open `http://127.0.0.1:8000`.

See [architecture.md](/Users/odysseaskoutrolikos/rtms/architecture.md), [agent.md](/Users/odysseaskoutrolikos/rtms/agent.md), [mvp_scope.md](/Users/odysseaskoutrolikos/rtms/mvp_scope.md), [docs/developer_setup.md](/Users/odysseaskoutrolikos/rtms/docs/developer_setup.md), and [docs/operator_guide.md](/Users/odysseaskoutrolikos/rtms/docs/operator_guide.md).

