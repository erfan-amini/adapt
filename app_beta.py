"""ADAPT Beta: the ADAPT app for temporary testing, with New York locations
only and no app password. Shinnecock's building-level pages still ask for the
project password.

Deploy on Streamlit Community Cloud with this file as the main file. It runs
app.py unchanged with ADAPT_VARIANT=beta (see adapt_core/config.py). The NSI
survey tool still asks for the survey password, because its Google Sheet
backend is shared with the team.
"""
import os
import runpy

os.environ["ADAPT_VARIANT"] = "beta"
runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), run_name="__main__")
