# Member 3 Dashboard | Warehouse Digital Twin

Streamlit visualization layer for Group 6's Automated Warehouse Sorting Line.

## Run locally (Windows PowerShell)

From the extracted project folder:

```powershell
python -m venv C:\whvenv
C:\whvenv\Scripts\python.exe -m pip install --upgrade pip
C:\whvenv\Scripts\python.exe -m pip install --no-cache-dir -r dashboard/requirements.txt
C:\whvenv\Scripts\python.exe -m streamlit run dashboard/app.py
```

Open `http://localhost:8501` in your browser. The separate virtual environment avoids conflicts with packages installed into the Windows Store Python environment.

## Pages

- **Overview:** operational KPIs, 2D warehouse layout, equipment state, bay occupancy and parcel records.
- **Analytics:** event distribution, conveyor speed observations, conveyor condition observations and event log.
- **Digital Twin Graph:** interactive Neo4j graph with entity-type filters, hover details and record inspection.
- **Alerts & Controls:** active/resolved alerts and simulator control/fault/dispatch commands.

## Data modes

- **Demo · sample data** reads the team's bundled JSON responses from `sample_data/`. It is intended for UI inspection and repeatable local demonstration. Controls are intentionally disabled in this mode.
- **Live · FastAPI** reads the REST endpoints through `api.py`. Set `API_BASE_URL` if the backend is not at `http://localhost:8000`.

PowerShell example:

```powershell
$env:API_BASE_URL = "http://localhost:8000"
C:\whvenv\Scripts\python.exe -m streamlit run dashboard/app.py
```

## Important interpretation notes

The dashboard does not invent telemetry. If a sample response contains only one conveyor-speed or jam-state observation, the corresponding chart will show that one observation and explain that more history is needed to establish a trend. Demo mode is not live simulator integration. The live controls require a running FastAPI service implementing the agreed `/api/control` contract.

## Basic validation

Run the bundled sample-data checks from the extracted project folder:

```powershell
C:\whvenv\Scripts\python.exe -m unittest discover -s dashboard/tests -v
```

These checks validate the sample payloads and graph structure. They do not replace end-to-end tests against the simulator and FastAPI backend.


Graph layout compatibility note: the dashboard uses NetworkX spring layout, which avoids the optional SciPy dependency required by Kamada-Kawai layout.


### KPI definitions
The Overview KPI cards expose help text with the calculation definitions. If the API provides KPI values, those are used; otherwise the dashboard derives throughput, routing accuracy, average sorting time and rejection rate from parcel records. Bay occupancy is calculated across all three bays. The bundled sample regression test checks that the displayed sample values match the underlying parcel records.
