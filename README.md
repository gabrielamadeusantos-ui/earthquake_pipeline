# 🌍 Real-Time Global Earthquake Monitoring — Data Engineering Project

End-to-end data pipeline designed to ingest, process, and visualize global earthquake data in near real-time using a fully serverless and cost-free stack.

Project link: <a href="https://datastudio.google.com/reporting/a4453d0c-f99f-4d4f-bdfd-2bc443434ed7" target="_blank">https://datastudio.google.com/reporting/a4453d0c-f99f-4d4f-bdfd-2bc443434ed7</a>

---

## 📌 Project Overview

This project demonstrates the design and implementation of a production-style data pipeline with the following capabilities:

* Real-time ingestion of seismic data from a public API
* Incremental data loading with deduplication logic
* Cloud-based storage using PostgreSQL
* Automated orchestration using CI/CD
* Public-facing interactive dashboard

---

## 🎥 Explanation Video

<p align="center">
  <a href="https://youtu.be/NdRpZVuqV8Y" target="_blank">
    <img src="https://img.youtube.com/vi/NdRpZVuqV8Y/0.jpg" alt=""/>
  </a>
</p>

> Click the image above to watch a short demo of the pipeline and dashboard in action.

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

* **Aggregation with Data Preservation**
  Although events are grouped, the pipeline still stores:

  * the **total number of occurrences per cluster**

---

### Impact

* Reduced storage requirements
* Improved PostgreSQL query performance
* Faster incremental loads and lower API usage
* Maintained analytical value through aggregated metrics

---

### Trade-off Considerations

* Loss of fine-grained geographic precision
* Suitable for **macro-level analysis**, not precise seismic research

---

## ⚙️ Core Features

### 1. Historical Data Load (Batch Ingestion)

* Extracts earthquakes with magnitude > 4.0 since 1996
* Implements **monthly pagination**
* Designed for **one-time execution**
* Optimized for bulk inserts

---

### 2. Incremental Data Pipeline

* Queries latest timestamp (`MAX(time)`)
* Fetches only new records
* Prevents duplicates
* Lightweight and efficient

---

### 3. Automation & Orchestration

* Fully automated via GitHub Actions
* Scheduled execution (e.g., hourly)
* CI/CD-based pipeline

---

### 4. Data Visualization

* Interactive dashboard via Looker Studio
* Geospatial mapping
* Trend and magnitude analysis
* Public access

---

## 🔄 Data Pipeline Flow

1. Scheduler triggers pipeline
2. Script fetches data from USGS API
3. Data is transformed and stored
4. Dashboard updates automatically

---

## 📂 Project Structure

```id="struct1"
├── scripts/
│   ├── historic_load.py
│   ├── incremental_load.py
│
├── .github/workflows/
│   └── pipeline.yml
│
├── requirements.txt
├── README.md
```

---

## ⚡ Engineering Decisions

* **Incremental Load Strategy** — minimizes API usage
* **Pagination Handling** — ensures full data coverage
* **Supabase PostgreSQL** — managed, scalable storage
* **GitHub Actions** — simple, cost-free orchestration

---

## 🧠 Challenges & Learnings

* Efficient API pagination
* Idempotent ingestion design
* Deduplication logic
* Building production-style pipelines with free tools

---

## 👨‍💻 Author

Gabriel Amadeu Santos

Data Analyst
