# Caspian & Black Sea Monitor

[![Pages](https://github.com/MonarchCastleTech/caspian-black-sea-monitor/actions/workflows/pipeline.yml/badge.svg)](https://github.com/MonarchCastleTech/caspian-black-sea-monitor/actions/workflows/pipeline.yml)

Autonomous 0–14 day escalation-pressure warning across the Caspian and Black Sea basins.

**Dashboard:** https://monarchcastletech.github.io/caspian-black-sea-monitor/
**Methodology:** https://monarchcastletech.github.io/caspian-black-sea-monitor/methodology/

## Model

The transparent index combines IMF PortWatch maritime flow (30%), official NATO posture language (25%), U.S. Treasury OFAC regional action velocity (20%), FRED commodity dislocation (15%), and MET Norway/ECMWF port weather (10%). Full formulas, failure handling, alert bands, and limitations appear on the methodology page.

GitHub Actions runs tests, refreshes public data, commits the evidence snapshot, and deploys GitHub Pages every six hours. No key, account, paid API, or generative AI is required.

## Reproduce

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python pipeline/caspian_black_sea_monitor_pipeline.py
python -m http.server 8000
```

Treat the index as a screening signal. It is not an event probability or live operational maritime picture.

Part of Monarch Castle Technologies. See [BRAND.md](BRAND.md) for approved asset use.
