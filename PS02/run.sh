#!/usr/bin/env sh
set -eu
python -m pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0
