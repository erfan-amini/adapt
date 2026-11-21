"""ADAPT-AI: the ADAPT app for temporary testing, with Mastic Beach and
Gloucester County only and no password.

Deploy on Streamlit Community Cloud with this file as the main file. It runs
app.py unchanged with ADAPT_VARIANT=ai (see adapt_core/config.py). The NSI
survey tool still asks for the survey password, because its Google Sheet
backend is shared with the team.
"""
import os
import runpy

os.environ["ADAPT_VARIANT"] = "ai"
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), run_name="__main__")
