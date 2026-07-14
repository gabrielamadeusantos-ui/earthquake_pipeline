# 🌍 Real-Time Global Earthquake Monitoring — Data Engineering Project

End-to-end data pipeline designed to ingest, process, and visualize global earthquake data in near real-time using a fully serverless and cost-free stack.

Project link: https://datastudio.google.com/reporting/a4453d0c-f99f-4d4f-bdfd-2bc443434ed7

---

## 📌 Project Overview

This project demonstrates the design and implementation of a production-style data pipeline with the following capabilities:

* Real-time ingestion of seismic data from a public API
* Incremental data loading with deduplication logic
* Cloud-based storage using PostgreSQL
* Automated orchestration using CI/CD
* Public-facing interactive dashboard

---

## 🧱 Architecture

```id="arch1"
USGS API → Python ETL → PostgreSQL (Supabase) → Looker Studio Dashboard
                      ↑
              GitHub Actions (Scheduler)
```

---

## 🛠️ Tech Stack

| Layer           | Technology                                      |
| --------------- | ----------------------------------------------- |
| Data Source     | USGS Earthquake API                             |
| Data Processing | Python (Pandas, Requests, SQLAlchemy, Psycopg2) |
| Database        | PostgreSQL (Supabase)                           |
| Orchestration   | GitHub Actions (CI/CD Scheduler)                |
| Visualization   | Google Looker Studio                            |

---
## ⚖️ Data Volume Optimization & Trade-offs

Due to the high volume of seismic data generated globally, it was necessary to implement filtering and aggregation strategies to ensure the pipeline remained efficient, scalable, and within the limits of a free-tier infrastructure.

### Key Decisions

* **Magnitude Threshold Filtering**
  Only earthquakes with magnitude **≥ 4.0** are ingested.
  This significantly reduces data volume while preserving events with higher analytical relevance.

* **Spatiotemporal Deduplication Strategy**
  Earthquakes occurring:

  * within the **same 1-hour window**, and
  * within the **same geographic area (rounded latitude/longitude, no decimal precision)**

  are treated as a **single event cluster**.

  This effectively groups nearby seismic activities into a broader spatial unit (square grid approximation), reducing noise and preventing excessive granularity.

* **Aggregation with Data Preservation**
  Although events are grouped, the pipeline still stores:

  * the **total number of occurrences per cluster**
  * enabling both **data reduction** and **analytical completeness**

---

### Impact

* Substantial reduction in storage requirements
* Improved query performance in PostgreSQL
* Faster incremental loads and lower API usage
* Maintained analytical value through aggregated metrics

---

### Trade-off Considerations

* Loss of fine-grained geographic precision
* Suitable for **macro-level analysis**, not for precise seismic research

---

## ⚙️ Core Features

### 1. Historical Data Load (Batch Ingestion)

* Extracts earthquakes with magnitude > 4.0 since 1996
* Implements **monthly pagination** to handle API limits
* Designed for **one-time execution**
* Optimized for bulk insert performance

---

### 2. Incremental Data Pipeline

* Queries latest timestamp from database (`MAX(time)`)
* Fetches only new records from API
* Prevents duplicates at ingestion level
* Lightweight and optimized for frequent execution

---

### 3. Automation & Orchestration

* Fully automated via GitHub Actions
* Scheduled execution (e.g., hourly updates)
* No manual intervention required
* CI/CD-based pipeline approach

---

### 4. Data Visualization

* Interactive dashboard built with Looker Studio
* Geospatial analysis using map visualizations
* Magnitude distribution and trend monitoring
* Public access (no authentication required)

---

## 🔄 Data Pipeline Flow

1. Scheduler triggers incremental pipeline
2. Script fetches new earthquake data from USGS API
3. Data is transformed and inserted into PostgreSQL
4. Dashboard reflects updated data automatically

---

## 📂 Project Structure

```id="struct1"
├── scripts/
│   ├── historic_load.py        # Batch ingestion (full load)
│   ├── incremental_load.py     # Incremental pipeline
│
├── .github/workflows/
│   └── pipeline.yml            # CI/CD automation
│
├── requirements.txt
├── README.md
```

---

## ⚡ Engineering Decisions

* **Incremental Load Strategy:**
  Reduces API usage and improves performance by only fetching new data

* **Pagination Handling:**
  Prevents API throttling and ensures full historical coverage

* **Cloud PostgreSQL (Supabase):**
  Enables scalable and managed storage with SQL access

* **CI/CD Orchestration:**
  GitHub Actions chosen for simplicity and zero cost

---

## 🧠 Challenges & Learnings

* Handling API pagination efficiently
* Designing idempotent data ingestion
* Avoiding duplication in incremental loads
* Structuring a production-like pipeline using free tools

---

## 👨‍💻 Author

Gabriel Amadeu Santos
Aspiring Data Engineer | Background in Audit & Analytics

---

## 📄 License

MIT License
