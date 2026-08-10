# 🌊 BlauEdge — Marine Ecosystem Recovery Monitor

> **HackUPC 2026** · EdgeAI for a Resilient and Greener Barcelona  
> Built with Arduino UNO Q · Edge Impulse · Python · HTML

---

## Inspiration

Recent efforts in Barcelona showed that over **100 local marine species returned to the coastline in just a few months**. That rapid recovery made us realise how resilient marine ecosystems can be — and also how little real-time data we have to monitor and guide that recovery.

Barcelona's breakwaters (*escolleres*) could be thriving underwater sanctuaries, but right now nobody is watching them. No one knows which sections are recovering and which are biological deserts. We built BlauEdge to change that.

---

## What It Does

BlauEdge is a real-time marine ecosystem monitoring system that evaluates the health of coastal zones along Barcelona's coastline using two inputs:

- **Temperature sensing** — detects thermal stress events that signal ecosystem decline
- **pH level** — monitors water acidity to detect acidification events that threaten shell-based marine life and coral health (currently simulated for prototype validation).
- **AI species detection** — uses a camera and an on-device Edge Impulse model to identify marine species on the breakwater surface

The results are streamed live to an **interactive web dashboard** with a real map of the Barcelona coastline, showing the health state of each monitored zone in real time.

A **simulation mode** keeps the dashboard fully functional even without hardware connected.

---

## How We Built It

We used the **Arduino App Lab** architecture to create a seamless hardware-to-browser experience:

1. The **Arduino UNO Q** (`sketch.ino`) reads the Modulino Thermo sensor and sends data to the board's internal Python environment via the Arduino Bridge.
2. A **Python Backend (`main.py`)** running directly on the board receives the hardware data, stores it in a Time-Series Database, handles the camera video stream, and serves a local web server via the App Lab `WebUI` library.
3. The **HTML Dashboard (`index.html`)** connects to the board's IP, polling the REST API for historical data and receiving real-time socket updates to power the live map and sensor readings.

---

## Hardware

| Component | Role |
|---|---|
| **Arduino UNO Q** | Main brain — runs the Python server, web UI, and AI models on-device |
| **Modulino Thermo** | Water temperature — detects thermal stress events |
| **USB Web Camera** | Captures breakwater surface for live video feed and species detection |

---

## AI Model

Built with **Edge Impulse Studio**, deployed on the Arduino UNO Q.

- **Type:** FOMO object detection (Faster Objects, More Objects)
- **Input:** Camera frames of the breakwater surface
- **Output:** Detected marine species present in the frame
- **Training data:** Manually collected and labelled images — no public dataset of local Barcelona coastal species exists, so we built our own from scratch.

The detected species count, combined with the temperature reading, feeds a simple classifier that assigns one of four health states:

| State | Meaning |
|---|---|
| 🟢 **HEALTHY** | Good temperature, species detected |
| 🔵 **RECOVERING** | Moderate conditions, some biological activity |
| 🔴 **STRESSED** | Temperature spike above threshold |
| ⚫ **INACTIVE** | No species detected, unfavourable conditions |

---

### API (Python → Browser)

| Endpoint | Method | What it returns |
|---|---|---|
| `/api/data` | GET | All node readings + notes |
| `/api/nodes` | POST | Add / update a node |
| `/api/notes` | POST | Add a field note |
| `/api/notes/<id>` | DELETE | Remove a note |

---

## Challenges

**Camera + Arduino OS clash** — connecting the Logitech USB camera to the Arduino caused driver conflicts due to OS incompatibilities. Took significant debugging to resolve.

**Arduino → browser in real time** — getting live hardware data into a browser was much harder than expected. The Python bridge layer was our solution, but building and debugging the full pipeline under time pressure was the hardest part of the project.

---

## Getting Started

### Requirements

- Python 3.10+
- Arduino UNO Q + Modulino Thermo + USB camera
- [Arduino App Lab IDE](https://docs.arduino.cc/software/app-lab/)
- [Edge Impulse Studio](https://studio.edgeimpulse.com/) (Optional, for retraining)

### Install & Run

1. Download the BlauEdge.zip file from the repository.
2. Open the Arduino App Lab environment.
3. Upload the BlauEdge.zip file to import the project into your workspace.
4. Plug in the Arduino UNO Q with the Modulino Thermo and USB Camera connected.
5. Click the Run button in App Lab to provision the board and start the Python server.
6. Open index.html to view the live dashboard!

(If the camera stream fails or isn't plugged in, the UI will gracefully fall back to a "No Video Signal" placeholder while keeping environmental data flowing!)

---

## What's Next

- **Real species dataset** — collect labelled images of actual native Barcelona coastal species for a much more accurate model
- **More sensors** — pH, light intensity, vibration/acoustic, salinity to build a full ecosystem fingerprint
- **Waterproof enclosure** — for real underwater breakwater deployment
- **Scale** — make BlauEdge replicable for any coastal city

---

## Team

| Name | Role |
|---|---|
| [Agustina Ciaponi] | Python bridge |
| [Emma Leroux Fernández-Armesto] | Arduino Hardware, Edge Impulse model |
| [Martí Amat] | Python bridge |
| [Guillem Arévalo Morell] | Dashboard + frontend |

**HackUPC 2026**

---

## License

see [LICENSE.txt](LICENSE.txt)

---

## Links

- 🌐 [Devpost](https://devpost.com/software/blauedge?ref_content=my-projects-tab&ref_feature=my_projects) 
- 🛠️ [Arduino Project Hub](https://projecthub.arduino.cc/projects/89865fd3-87e5-4fde-a297-a40eb8f39453/preview?_gl=1*yami0d*_up*MQ..*_ga*MTA4OTk0MDA2LjE3NzcyMDU4NTE.*_ga_NEXN8H46L5*czE3NzcyMDU4NTAkbzEkZzAkdDE3NzcyMDU4NTAkajYwJGwwJGgyMDI0MDI4NjM./)
- 📹 [Demo Video](https://youtu.be/FScRb-mFu6M)

---

*Built with ❤️ for Barcelona's coast at HackUPC 2026*
*"The ocean is not a dump. It's a garden." — Let's tend it.*
