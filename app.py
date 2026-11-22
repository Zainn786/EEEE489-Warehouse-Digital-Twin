import time
from datetime import datetime
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import networkx as nx
from kpi import calculate_kpis
from streamlit_autorefresh import st_autorefresh

from api import (
    API_BASE_URL, APIError, get_health, get_state, get_parcels,
    get_metrics, get_history, get_graph, get_alerts, send_control
)

st.set_page_config(page_title="Warehouse Digital Twin | Group 6", page_icon="📦",
                   layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
:root { --navy:#142b3d; --navy2:#1d3b50; --teal:#2b8c86; --gold:#d8a64b; --ink:#203342; --muted:#738391; --line:#e4ebef; }
.stApp {background:linear-gradient(135deg,#f4f7f8 0%,#eef3f5 100%); color:var(--ink);}
[data-testid="stSidebar"] {background:linear-gradient(180deg,#142b3d 0%,#1b3b50 100%); border-right:1px solid #29495e;}
[data-testid="stSidebar"] * {color:#f4f7f9;}
[data-testid="stSidebar"] [data-testid="stRadio"] label {padding:5px 7px;border-radius:8px;}
.block-container {padding-top:1.8rem; padding-bottom:2.4rem; max-width:1680px;}
h1 {color:#172f42; letter-spacing:-.035em; font-weight:800;}
h2,h3 {color:#203b4e; letter-spacing:-.02em;}
[data-testid="stMetric"] {background:linear-gradient(145deg,#ffffff 0%,#f9fbfc 100%);border:1px solid #e1e9ed;
  padding:17px 18px;border-radius:15px;box-shadow:0 4px 14px rgba(20,43,61,.045); min-height:112px;}
[data-testid="stMetricLabel"] {color:#6e7e89;font-weight:700;font-size:.74rem;letter-spacing:.055em;}
[data-testid="stMetricValue"] {color:#17364a;font-weight:750;}
[data-testid="stMetricDelta"] {font-size:.78rem;}
div[data-testid="stVerticalBlockBorderWrapper"] {border-color:#e1e9ed!important;border-radius:15px!important;box-shadow:0 3px 14px rgba(20,43,61,.035);}
.section-title {font-size:1rem;font-weight:800;color:#203b4e;margin:4px 0 12px;letter-spacing:.045em;}
.muted,.small-note {color:#738391;font-size:.84rem;}
div.stButton > button {border-radius:10px;border:1px solid #d9e3e8;font-weight:650;min-height:42px;}
div.stButton > button[kind="primary"] {background:#1f6e78;border-color:#1f6e78;color:white;}
[data-testid="stAlert"] {border-radius:12px;}
[data-testid="stDataFrame"] {border:1px solid #e2e9ed;border-radius:12px;overflow:hidden;}
hr {border-color:#e2e9ed;}
</style>
""", unsafe_allow_html=True)

def unwrap(data, key):
    if isinstance(data, dict) and key in data: return data[key]
    return data if isinstance(data, list) else []

def safe_num(obj, key, default=0):
    try: return float(obj.get(key, default) or 0)
    except (AttributeError, TypeError, ValueError): return default

def status_color(status):
    s=str(status or "").lower()
    if any(x in s for x in ("jam", "fault", "full", "critical", "offline", "reject")): return "#c84b4b"
    if any(x in s for x in ("warning", "paused", "busy")): return "#d69a32"
    if any(x in s for x in ("running", "operational", "available", "online", "ready")): return "#27845e"
    return "#73818b"

def draw_warehouse(state, parcels):
    fig=go.Figure()
    # Conveyor track and processing stations
    fig.add_shape(type="rect",x0=1,y0=3.2,x1=8.8,y1=4.4,line=dict(color="#718491",width=2),
                  fillcolor="#dce4e8")
    stations=[
        (1.0,3.0,1.1,1.6,"ENTRY"),(3.0,3.0,1.1,1.6,"SCANNER"),
        (5.0,3.0,1.1,1.6,"SORTER")]
    for x,y,w,h,label in stations:
        fig.add_shape(type="rect",x0=x,y0=y,x1=x+w,y1=y+h,line=dict(color="#536b7a",width=1.5),
                      fillcolor="#ffffff")
        fig.add_annotation(x=x+w/2,y=y+h/2,text=label,font=dict(size=11,color="#263b49"),showarrow=False)
    bay_data={str(b.get("bay_id")):b for b in state.get("bays",[]) if isinstance(b,dict)}
    for idx,bay in enumerate(["A","B","C"]):
        y=5.7-idx*1.8
        b=bay_data.get(bay,{})
        occ=int(safe_num(b,"occupancy")); cap=int(safe_num(b,"capacity",5))
        color="#c84b4b" if cap and occ>=cap else "#27845e"
        fig.add_shape(type="rect",x0=10,y0=y-0.55,x1=12.5,y1=y+0.55,
                      line=dict(color=color,width=2),fillcolor="#ffffff")
        fig.add_annotation(x=11.25,y=y,text=f"BAY {bay} · {occ}/{cap}",showarrow=False,
                           font=dict(size=12,color=color))
        fig.add_shape(type="line",x0=6.1,y0=3.8,x1=9.9,y1=y,line=dict(color="#718491",width=2))
    fig.add_shape(type="rect",x0=1,y0=0.25,x1=3.4,y1=1.15,line=dict(color="#c84b4b",width=2),
                  fillcolor="#fff7f7")
    fig.add_annotation(x=2.2,y=.7,text="REJECT LANE",showarrow=False,font=dict(color="#a33b3b",size=11))
    # Parcel positions by current_stage
    positions={"ENTRY":(1.55,3.8),"SCANNER":(3.55,3.8),"SORTER":(5.55,3.8),
               "BAY_A":(10.5,5.7),"BAY_B":(10.5,3.9),"BAY_C":(10.5,2.1),
               "REJECT_LANE":(2.2,.7)}
    for p in parcels:
        stage=str(p.get("current_stage") or "ENTRY").upper()
        x,y=positions.get(stage,(1.55,3.8))
        fig.add_trace(go.Scatter(x=[x],y=[y],mode="markers+text",
            marker=dict(size=17,color="#e0a43c",line=dict(color="#ffffff",width=2)),
            text=[str(p.get("parcel_id","Parcel"))],textposition="top center",
            textfont=dict(size=10,color="#263b49"),name=str(p.get("parcel_id","Parcel")),
            hovertemplate=f"{p.get('parcel_id','')}<br>Stage: {stage}<br>Target: {p.get('target_bay','-')}<extra></extra>"))
    conv=state.get("conveyor",{})
    fig.update_layout(height=390,margin=dict(l=5,r=5,t=10,b=5),paper_bgcolor="white",
        plot_bgcolor="white",showlegend=False,xaxis=dict(visible=False,range=[0,13.3]),
        yaxis=dict(visible=False,range=[0,7]),hovermode="closest")
    return fig

def show_metric(label, value, suffix="", help_text=None):
    st.metric(label, f"{value}{suffix}", help=help_text)

st.sidebar.markdown("## 📦 WAREHOUSE TWIN")
st.sidebar.caption("GROUP 6 · AUTOMATED SORTING LINE")
data_mode = st.sidebar.radio("DATA SOURCE", ["Demo · sample data", "Live · FastAPI"], index=0)
demo_mode = data_mode.startswith("Demo")
page=st.sidebar.radio("NAVIGATION",["Overview","Analytics","Digital Twin Graph","Alerts & Controls"])
auto=st.sidebar.toggle("Auto-refresh (5 seconds)", value=True)
if auto:
    st_autorefresh(interval=5000, key="warehouse_dashboard_refresh")
    st.sidebar.caption("Dashboard refreshes every 5 seconds.")
else:
    st.sidebar.caption("Automatic refresh is paused.")
st.sidebar.markdown("---")
st.sidebar.caption(f"API: {API_BASE_URL}")

head_a, head_b = st.columns([4,1])
with head_a:
    st.title("Warehouse Operations")
    st.caption("Digital twin monitoring · Automated parcel sorting line · Group 6")
with head_b:
    st.markdown("<div style='text-align:right;padding-top:18px;color:#738391;font-size:.82rem'>CONTROL ROOM<br><b style='color:#2b8c86'>● SYSTEM VIEW</b></div>", unsafe_allow_html=True)

# Demo mode loads the team's supplied Neo4j sample responses. Live mode reads only
# through the agreed FastAPI REST interface.
if demo_mode:
    import json
    from pathlib import Path
    sample_dir = Path(__file__).parent / "sample_data"
    def load_sample(filename):
        with (sample_dir / filename).open(encoding="utf-8") as f:
            return json.load(f)
    data = {
        "state": load_sample("get_state.json"),
        "parcels": load_sample("get_parcels.json"),
        "history": load_sample("get_telemetry_events.json"),
        "alerts": load_sample("get_alerts.json"),
        "graph": load_sample("get_graph.json"),
        "health": {"status": "demo", "source": "bundled sample data"},
        "metrics": {},
    }
    st.info("Demo mode: displaying the team's supplied sample dataset. Controls are disabled and values are not live.")
else:
    data = {}
    errors = []
    for key, fn in [
        ("health",get_health),("state",get_state),("parcels",get_parcels),
        ("metrics",get_metrics),("history",get_history),("graph",get_graph),("alerts",get_alerts)
    ]:
        try:
            data[key] = fn()
        except APIError as e:
            errors.append(str(e))
    if errors:
        with st.container(border=True):
            st.error("Backend connection is incomplete. Some live data could not be loaded.")
            st.caption(" · ".join(errors[:3]))
            st.info("Check that the FastAPI service is running and API_BASE_URL points to it.")

state=data.get("state",{}) if isinstance(data.get("state",{}),dict) else {}
metrics=data.get("metrics",{}) if isinstance(data.get("metrics",{}),dict) else {}
if isinstance(metrics.get("kpis"),dict): metrics=metrics["kpis"]
if not metrics: metrics=state.get("kpis",{})
if not isinstance(metrics,dict): metrics={}
if not isinstance(state.get("bays",[]),list): state["bays"]=[]
parcels=unwrap(data.get("parcels",{}),"parcels")
if not parcels: parcels=state.get("active_parcels",[])
if not isinstance(parcels,list): parcels=[]
active_parcels=[p for p in parcels if str(p.get("status","")).lower() not in ("dispatched",)]
history=unwrap(data.get("history",{}),"events")
alerts=unwrap(data.get("alerts",{}),"alerts")
health=data.get("health",{})
simulator_status=state.get("simulator_status","unknown")

if page=="Overview":
    cols=st.columns(5)
    # Overall occupancy is calculated across all three bays, rather than
    # reporting Bay A alone as if it represented the whole warehouse.
    bay_rows=[b for b in state.get("bays",[]) if isinstance(b,dict)]
    total_capacity=sum(safe_num(b,"capacity") for b in bay_rows)
    total_occupied=sum(safe_num(b,"occupancy") for b in bay_rows)
    overall_occupancy=(round(100*total_occupied/total_capacity,1) if total_capacity else "—")
    # Use the API/state KPI values when supplied. If absent, derive the same
    # metrics from parcel records using documented, auditable definitions.
    derived_kpis = calculate_kpis(parcels, state.get("bays", []))
    state_kpis = state.get("kpis", {}) if isinstance(state.get("kpis", {}), dict) else {}
    kpi_source = {**derived_kpis, **state_kpis, **metrics}
    kpi_specs=[
        ("THROUGHPUT", kpi_source.get("throughput", "—"), " parcels",
         "Distinct parcels whose latest status is sorted or dispatched."),
        ("ROUTING ACCURACY", kpi_source.get("routing_accuracy", "—"), "%",
         "Correctly routed parcels divided by parcels with a recorded routing outcome."),
        ("AVG SORTING TIME", kpi_source.get("average_sorting_time", "—"), " s",
         "Mean time from parcel creation to sorting, for parcels with both timestamps."),
        ("BAY OCCUPANCY", overall_occupancy, "%",
         "Combined occupied slots divided by combined capacity across Bays A, B and C."),
        ("REJECTION RATE", kpi_source.get("rejection_rate", "—"), "%",
         "Rejected parcels divided by terminal parcel outcomes: sorted, rejected or dispatched."),
    ]
    for c,(label,val,suffix,definition) in zip(cols,kpi_specs):
        with c:
            show_metric(label,val,suffix,help_text=definition)
    left,right=st.columns([2.2,1])
    with left:
        st.markdown('<div class="section-title">2D WAREHOUSE FLOOR PLAN</div>',unsafe_allow_html=True)
        st.plotly_chart(draw_warehouse(state,active_parcels),width="stretch")
    with right:
        st.markdown('<div class="section-title">EQUIPMENT STATUS</div>',unsafe_allow_html=True)
        for label,obj in [("Conveyor",state.get("conveyor",{})),("Scanner",state.get("scanner",{})),("Sorter",state.get("sorter",{}))]:
            status=obj.get("status","unknown")
            st.markdown(f"**{label}**")
            st.markdown(f"<span style='color:{status_color(status)}'>● {status.upper()}</span>",unsafe_allow_html=True)
            if label=="Conveyor":
                st.caption(f"Speed: {obj.get('speed_mps','—')} m/s · Jammed: {obj.get('jammed','—')}")
        st.markdown("**Dispatch bays**")
        for b in state.get("bays",[]):
            st.progress(min(1.0,safe_num(b,"occupancy")/max(1,safe_num(b,"capacity",5))),
                        text=f"Bay {b.get('bay_id')}: {b.get('occupancy',0)}/{b.get('capacity',5)}")
    st.markdown("### Parcel status overview")
    if active_parcels:
        st.dataframe(pd.DataFrame(active_parcels),width="stretch",hide_index=True)
    else: st.info("No parcel records returned by the backend.")
    st.caption(f"Simulator: {simulator_status} · Run: {state.get('run_id','—')}")

elif page=="Analytics":
    st.subheader("Operational analytics")
    st.caption("Historical telemetry and event patterns from the selected warehouse run.")
    if history:
        df=pd.DataFrame(history)
        if "timestamp" in df:
            df["timestamp"]=pd.to_datetime(df["timestamp"],errors="coerce",utc=True)
            df=df.dropna(subset=["timestamp"]).sort_values("timestamp")
            df["time_label"]=df["timestamp"].dt.strftime("%H:%M:%S")
        else:
            df["time_label"]=[f"Event {i+1}" for i in range(len(df))]
        a,b,c=st.columns(3)
        with a: st.metric("TELEMETRY EVENTS",len(df))
        with b: st.metric("EVENT TYPES",df["event_type"].nunique() if "event_type" in df else "—")
        with c: st.metric("PARCELS REFERENCED",df["parcel_id"].dropna().nunique() if "parcel_id" in df else "—")
        if "event_type" in df:
            counts=df["event_type"].value_counts().rename_axis("Event type").reset_index(name="Events")
            fig=px.bar(counts,x="Event type",y="Events",text="Events",color="Events",
                       color_continuous_scale=["#b9d7d5","#247c80"])
            fig.update_traces(textposition="outside",marker_line_width=0)
            fig.update_layout(height=340,showlegend=False,coloraxis_showscale=False,
                margin=dict(l=18,r=18,t=25,b=65),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="white",
                xaxis_title=None,yaxis_title="Number of events",font=dict(color="#536675"),
                yaxis=dict(gridcolor="#e9eef1",zeroline=False))
            st.plotly_chart(fig,width="stretch")
        # Extract only values actually present in telemetry. No synthetic points are added.
        if "data" in df:
            df["speed_mps"]=df["data"].apply(lambda x: x.get("speed_mps") if isinstance(x,dict) else None)
            speed=df.dropna(subset=["speed_mps"]).copy()
            if not speed.empty:
                st.markdown("#### Conveyor speed history")
                sf=px.line(speed,x="time_label",y="speed_mps",markers=True,
                           labels={"time_label":"Event time","speed_mps":"Speed (m/s)"})
                sf.update_traces(line=dict(color="#247c80",width=3),marker=dict(size=9,color="#d8a64b"))
                sf.update_layout(height=300,margin=dict(l=15,r=15,t=15,b=45),paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="white",font=dict(color="#536675"),xaxis=dict(type="category",title=None),
                    yaxis=dict(gridcolor="#e9eef1",rangemode="tozero"))
                st.plotly_chart(sf,width="stretch")
                if len(speed)<3:
                    st.caption(f"Only {len(speed)} conveyor-speed observation(s) exist in the supplied sample. A longer history is needed to demonstrate a trend.")
            df["jammed"]=df["data"].apply(lambda x: x.get("jammed") if isinstance(x,dict) else None)
            jam=df.dropna(subset=["jammed"]).copy()
            if not jam.empty:
                st.markdown("#### Conveyor condition history")
                jam["Condition"]=jam["jammed"].map({True:"Jammed",False:"Running"})
                jf=px.scatter(jam,x="time_label",y="Condition",color="Condition",
                    color_discrete_map={"Jammed":"#c45b5b","Running":"#2b8c86"},
                    labels={"time_label":"Event time"})
                jf.update_traces(marker=dict(size=13,line=dict(width=1,color="white")))
                jf.update_layout(height=250,margin=dict(l=15,r=15,t=15,b=40),paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="white",font=dict(color="#536675"),xaxis=dict(type="category",title=None),
                    yaxis=dict(title=None,categoryorder="array",categoryarray=["Running","Jammed"]))
                st.plotly_chart(jf,width="stretch")
                if len(jam)<3:
                    st.caption(f"Only {len(jam)} conveyor-condition observation(s) exist in the supplied sample.")
        st.markdown("#### Event log")
        display_cols=[c for c in ["timestamp","event_type","source_id","parcel_id","data"] if c in df.columns]
        view=df[display_cols].copy()
        if "timestamp" in view: view["timestamp"]=view["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        st.dataframe(view,width="stretch",hide_index=True)
    else: st.info("No historical telemetry is available yet.")
    st.caption("Charts use only records returned by /api/history or the team's bundled sample. Missing historical observations are not fabricated.")

elif page=="Digital Twin Graph":
    st.subheader("Neo4j knowledge graph")
    st.caption("Explore how warehouse assets, parcels, sensors and alerts are connected.")
    graph=data.get("graph",{})
    nodes=graph.get("nodes",[]) if isinstance(graph,dict) else []
    edges=graph.get("edges",[]) if isinstance(graph,dict) else []
    if nodes:
        node_types=sorted({str(n.get("type","Other")) for n in nodes})
        selected_types=st.multiselect("Show entity types",node_types,default=node_types)
        visible=[n for n in nodes if str(n.get("type","Other")) in selected_types]
        visible_ids={n.get("id") for n in visible}
        visible_edges=[e for e in edges if e.get("source") in visible_ids and e.get("target") in visible_ids]
        m1,m2,m3=st.columns(3)
        m1.metric("VISIBLE ENTITIES",len(visible))
        m2.metric("VISIBLE RELATIONSHIPS",len(visible_edges))
        m3.metric("ENTITY TYPES",len({n.get("type") for n in visible}))
        G=nx.Graph()
        for n in visible: G.add_node(n.get("id"),label=n.get("label"),type=n.get("type"),properties=n.get("properties",{}))
        for e in visible_edges: G.add_edge(e["source"],e["target"],relation=e.get("type","RELATED_TO"))
        if G.number_of_nodes():
            pos=nx.spring_layout(G, seed=17, k=1.15, iterations=100)
            edge_x=[];edge_y=[];edge_hover=[]
            for u,v,attrs in G.edges(data=True):
                x0,y0=pos[u];x1,y1=pos[v]
                edge_x += [x0,x1,None]; edge_y += [y0,y1,None]
                edge_hover.append(f"{G.nodes[u].get('label')} → {attrs.get('relation')} → {G.nodes[v].get('label')}")
            fig=go.Figure()
            fig.add_trace(go.Scatter(x=edge_x,y=edge_y,mode="lines",line=dict(width=1.5,color="#b8c7ce"),hoverinfo="skip"))
            palette={"Warehouse":"#193d55","Conveyor":"#2b8c86","Scanner":"#d5a044","Sorter":"#668db0",
                     "DispatchBay":"#32936f","Parcel":"#e3a43d","Alert":"#c45b5b","Sensor":"#8a78aa","RejectLane":"#c87575"}
            for typ in sorted({G.nodes[n].get("type","Other") for n in G.nodes}):
                ids=[n for n in G.nodes if G.nodes[n].get("type","Other")==typ]
                xs=[pos[n][0] for n in ids];ys=[pos[n][1] for n in ids]
                labels=[str(G.nodes[n].get("label",n)) for n in ids]
                hover=[]
                for n in ids:
                    props=G.nodes[n].get("properties",{})
                    detail="<br>".join(f"{k}: {v}" for k,v in list(props.items())[:7])
                    hover.append(f"<b>{G.nodes[n].get('label')}</b><br>Type: {typ}<br>{detail}")
                fig.add_trace(go.Scatter(x=xs,y=ys,mode="markers+text",name=typ,text=labels,
                    textposition="top center",textfont=dict(size=10,color="#344b5b"),hovertext=hover,hoverinfo="text",
                    marker=dict(size=21,color=palette.get(typ,"#8797a1"),line=dict(width=2,color="white"))))
            fig.update_layout(height=650,margin=dict(l=20,r=20,t=25,b=20),paper_bgcolor="white",plot_bgcolor="white",
                hovermode="closest",legend=dict(orientation="h",yanchor="bottom",y=1.02,x=0),
                xaxis=dict(visible=False),yaxis=dict(visible=False),font=dict(color="#536675"))
            st.plotly_chart(fig,width="stretch",config={"displaylogo":False})
            st.caption(f"Showing {len(visible)} of {len(nodes)} entities · {len(visible_edges)} of {len(edges)} relationships · Run {graph.get('run_id','—')}")
        with st.expander("Inspect entity and relationship records"):
            ndf=pd.DataFrame(visible)
            if "properties" in ndf: ndf["properties"]=ndf["properties"].apply(lambda x: str(x))
            st.markdown("**Entities**")
            st.dataframe(ndf,width="stretch",hide_index=True)
            st.markdown("**Relationships**")
            st.dataframe(pd.DataFrame(visible_edges),width="stretch",hide_index=True)
    else: st.info("The graph endpoint has not returned any nodes yet.")

elif page=="Alerts & Controls":
    st.subheader("Simulation controls")
    st.caption("Commands are sent to FastAPI, which should forward them to the simulator through MQTT.")
    if demo_mode:
        st.warning("Controls are unavailable in Demo mode. Switch DATA SOURCE to Live · FastAPI to enable commands.")
    c1,c2,c3,c4=st.columns(4)
    for col,cmd,label in [(c1,"start","▶ Start"),(c2,"pause","Ⅱ Pause"),(c3,"resume","↻ Resume"),(c4,"reset","Reset")]:
        with col:
            if st.button(label,width="stretch",disabled=demo_mode):
                try:
                    send_control(cmd);st.success(f"{cmd} command sent");time.sleep(.3);st.rerun()
                except APIError as e: st.error(str(e))
    st.markdown("#### Fault injection")
    f1,f2,f3=st.columns(3)
    for col,cmd,label in [(f1,"inject_jam","Inject conveyor jam"),(f2,"clear_jam","Clear conveyor jam"),(f3,"fail_next_scan","Fail next scan")]:
        with col:
            if st.button(label,width="stretch",disabled=demo_mode):
                try: send_control(cmd);st.success(f"{cmd} command sent")
                except APIError as e: st.error(str(e))
    st.markdown("#### Dispatch bay actions")
    bay=st.selectbox("Select dispatch bay",["A","B","C"])
    b1,b2=st.columns(2)
    with b1:
        if st.button("Fill selected bay",width="stretch",disabled=demo_mode):
            try: send_control("fill_bay",bay);st.success(f"fill_bay {bay} sent")
            except APIError as e: st.error(str(e))
    with b2:
        if st.button("Dispatch selected bay",width="stretch",disabled=demo_mode):
            try: send_control("dispatch_bay",bay);st.success(f"dispatch_bay {bay} sent")
            except APIError as e: st.error(str(e))
    st.divider()
    st.subheader("System alerts")
    if alerts:
        adf=pd.DataFrame(alerts)
        active=adf[adf["active"]==True] if "active" in adf else adf
        resolved=adf[adf["active"]==False] if "active" in adf else pd.DataFrame()
        ac,rc=st.columns(2)
        with ac: st.metric("ACTIVE ALERTS",len(active))
        with rc: st.metric("RESOLVED ALERTS",len(resolved))
        st.markdown("**Active alerts**")
        if len(active):
            for _,alert in active.iterrows():
                sev=str(alert.get("severity","warning")).lower()
                color="#bd4f4f" if sev in ("critical","high") else "#c18b32"
                with st.container(border=True):
                    left,right=st.columns([4,1])
                    with left:
                        st.markdown(f"<span style='color:{color};font-weight:800'>● {sev.upper()}</span> &nbsp; <b>{alert.get('type','System alert').replace('_',' ').title()}</b>",unsafe_allow_html=True)
                        st.write(str(alert.get("message","No message supplied")))
                        st.caption(f"ID: {alert.get('alert_id','—')} · Triggered: {alert.get('triggered_at','—')}")
                    with right:
                        st.markdown(f"<div style='text-align:right;color:{color};font-weight:800;padding-top:8px'>{'ACTIVE' if alert.get('active') else 'RESOLVED'}</div>",unsafe_allow_html=True)
        else: st.success("No active alerts. The system has no unresolved warnings.")
        with st.expander(f"Resolved alerts · {len(resolved)}"):
            if len(resolved):
                cols=[c for c in ["alert_id","type","severity","message","triggered_at","resolved_at"] if c in resolved]
                st.dataframe(resolved[cols],width="stretch",hide_index=True)
            else: st.caption("No resolved alerts returned.")
    else: st.success("No alerts returned by the backend.")

st.markdown("---")
st.caption(f"Group 6 · Warehouse Digital Twin · Last page load: {datetime.now().strftime('%H:%M:%S')}")
