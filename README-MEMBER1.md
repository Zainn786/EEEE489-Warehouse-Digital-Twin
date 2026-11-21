# Group 6 / Member 1 Simulator

This is the **Member 1** simulation + MQTT component. It conforms to Member 2's
`backend/graph/README.md`, `ontology.py` and `updates.py`.

## Preexisting files

Do **not** overwrite the team's existing `docker-compose.yml`, `infra/mosquitto.conf`,
`.gitignore`, or `backend/graph/`. An optional `compose.simulator.yaml` overlay is
provided for containerizing the simulator. It assumes the base compose includes
a service named `mosquitto`.

## Local development (Windows PowerShell; from repository root)

1. Make sure `docker compose up -d mosquitto` has succeeded.
2. Run `./.venv/Scripts/python.exe -m pytest tests/simulator -q`.
3. To start the simulator locally without the dashboard:
   `./.venv/Scripts/python.exe -m simulator.main --auto-start`
4. In another terminal, observe the MQTT messages:
   `docker compose exec mosquitto mosquitto_sub -h localhost -t 'warehouse/#' -v`
5. Stop local simulator using Ctrl+C.

To run only via dashboard commands, omit `--auto-start`, then publish
JSON to `warehouse/control/commands` (examples below).

## Commands (Windows PowerShell-friendly)

Run the simulator **without** `--auto-start` in Terminal 1, then publish commands
from Terminal 2:

```powershell
.\.venv\Scripts\python.exe -m simulator.send_command start
.\.venv\Scripts\python.exe -m simulator.send_command pause
.\.venv\Scripts\python.exe -m simulator.send_command resume
.\.venv\Scripts\python.exe -m simulator.send_command inject_jam
.\.venv\Scripts\python.exe -m simulator.send_command clear_jam
.\.venv\Scripts\python.exe -m simulator.send_command fail_next_scan
.\.venv\Scripts\python.exe -m simulator.send_command fill_bay A
.\.venv\Scripts\python.exe -m simulator.send_command dispatch_bay A
.\.venv\Scripts\python.exe -m simulator.send_command reset
```

Each publishes to `warehouse/control/commands`; the dashboard will eventually
send the same JSON through Member 4's FastAPI service.

The `fill_bay` scenario directs future parcels to selected bay and suppresses auto-dispatch
until manually dispatched. Overflow parcels are rejected with reason `bay_full`.

## Containerized simulator (optional)

`docker compose -f docker-compose.yml -f compose.simulator.yaml up --build -d simulator`

`docker compose -f docker-compose.yml -f compose.simulator.yaml logs -f simulator`

Stop it before running the local Python simulator, or two independent simulators
will publish events concurrently.

## MQTT topics

- `warehouse/events/parcel` parcel events
- `warehouse/events/equipment` conveyor/scanner/sorter
- `warehouse/events/bay` occupancy
- `warehouse/simulator/status` simulator status
- `warehouse/control/commands` JSON dashboard control commands (subscribed)

## Compatibility

- Every event has `event_id`, `run_id`, `event_type`, `timestamp`, `source_id`,
  `parcel_id`, `data`.
- A fresh UUID is assigned per event and per reset/run. UTC ISO 8601 timestamps.
- Parcel IDs restart at P001 for each new run, unique by (`run_id`, `parcel_id`).
- Equipment IDs: `CONVEYOR_01`, `SCANNER_01`, `SORTER_01`, `BAY_A/B/C`, `SIMULATOR`.
- `simulator_status` has `data.status = stopped|running|paused`.
- Jammed conveyor event uses `running:true, jammed:true, speed_mps:0.0`.
- Member 4 should subscribe to `warehouse/events/#` **and**
  `warehouse/simulator/status` (or `warehouse/#` excluding command topics),
  then call Member 2's `apply_event()` on valid events.

## Responsibilities and limitations

- Member 1 **publishes** events and responds to MQTT commands; Member 4 ingests,
  validates, calculates KPIs and alerts. Member 2 stores/queries Neo4j state.
- This prototype uses a *logical* clock. Pausing freezes parcel movement and
  bay auto-dispatch; conveyor jam stops conveyor progression but bay auto-dispatch
  can continue independently.
- Event generation is deterministic given random seed, but UUIDs/timestamps vary.
- The standard simulator does not deliberately misroute parcels; Member 4 may
  implement a dedicated misrouting test for routing accuracy.

## Optional local Neo4j infrastructure (Member 2)

The included `compose.neo4j.yaml` is an **optional overlay** for Member 2's graph
service. Do not use it if another team member has already placed a `neo4j`
service in the main compose file.

Create a local `.env` containing `NEO4J_PASSWORD=<your-own-local-password>`;
ensure `.env` remains Git-ignored. Run from the project root:

```powershell
docker compose -f docker-compose.yml -f compose.neo4j.yaml up -d neo4j
```

Neo4j Browser: http://localhost:7474. The `backend/graph/` Python modules
must be available locally (on the Neo4j feature branch or after integration).
There is no need to set up Neo4j to test Member 1's simulator alone.
