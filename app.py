"""Streamlit UI. No logic here — upload, run, show, download."""

import streamlit as st

import excel_io
import pipeline

st.set_page_config(page_title="SunSystems Renewal Radar", layout="wide")
st.title("SunSystems Renewal Radar")
st.caption("Upload your account list. The tool scores each account 1-5 and writes an analysis.")

uploaded = st.file_uploader("Account list (.xlsx)", type="xlsx")
force_refresh = st.sidebar.checkbox("Force refresh (ignore cache)", value=False)

if not uploaded:
    st.stop()

try:
    workbook = excel_io.load(uploaded)
    accounts = excel_io.read_accounts(workbook)
except excel_io.TemplateError as exc:
    st.error(f"That file doesn't match the template: {exc}")
    st.stop()

st.success(f"{len(accounts)} accounts found.")
st.dataframe(
    [{"Row": a["row"], "Client Name": a["name"], "Contact": a["contact"]} for a in accounts],
    use_container_width=True,
    hide_index=True,
)

if st.button("Run query", type="primary"):
    progress = st.progress(0.0, text="Researching...")
    done = []

    def on_result(result):
        done.append(result)
        progress.progress(len(done) / len(accounts), text=f"{len(done)}/{len(accounts)} — {result['name']}")

    results = pipeline.run_batch(accounts, force_refresh=force_refresh, on_result=on_result)
    progress.empty()

    excel_io.write_results(
        workbook,
        {r["row"]: {"heat": r["heat"] or 1, "analysis": r["analysis"]} for r in results},
    )
    st.session_state["output"] = excel_io.to_bytes(workbook)
    st.session_state["results"] = results

for result in st.session_state.get("results", []):
    heat = result.get("heat")
    with st.expander(f"{heat if heat else '—'}  ·  {result['name']}", expanded=False):
        if result.get("error"):
            st.error(result["error"])
            continue
        st.write(result["analysis"])
        st.caption(" · ".join(result["scored"]["reasons"]))
        for source in result["findings"].get("sources", []):
            if isinstance(source, dict) and source.get("url"):
                st.markdown(f"- [{source.get('title') or source['url']}]({source['url']})")

if st.session_state.get("output"):
    st.download_button(
        "Download completed workbook",
        data=st.session_state["output"],
        file_name="renewal-radar-results.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
