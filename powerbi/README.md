# Power BI Desktop setup

## Included native project

Open `CustomerIntelligence/CustomerIntelligence.pbip` in a current Power BI Desktop version with project support. The report contains overview, retention, and evaluation pages, with data embedded in the semantic model's Power Query partitions so no machine-specific file path is required. Refresh to load the synthetic snapshot.

Run `python -m analytics.pipeline` and `python -m analytics.powerbi` from the repository root to regenerate it. Export refuses user-supplied datasets to avoid accidentally embedding personal customer information in a public report. The generated metadata passed validation against Microsoft's JSON schemas. Desktop rendering/refresh still needs verification; schema validation alone does not prove every visual renders correctly.

The monthly revenue table is independent of the customer snapshot; the customer segment slicer deliberately does not filter the full-dataset revenue trend. The evaluation page contains held-out aggregate metrics; customer rows labeled exploration-only are not additional test evidence.

## Connecting your own local exports

Generate the artifacts first. In Power BI Desktop, choose Get data → Text/CSV and import `artifacts/customers.csv`, `artifacts/monthly_revenue.csv`, and `artifacts/retention.csv`. Keep them as independent tables unless you deliberately model compatible dimensions; joining snapshot customer rows to aggregated month rows would multiply totals.

Use the names `customers`, `monthly_revenue`, and `retention`. Set monetary/risk/retention columns to decimal numbers, counts to whole numbers, and month/cohort to text (YYYY-MM sorts chronologically).

Create the measures in `measures.dax` on the customers table. Build these pages:

1. **Overview**: selected-customer count, observed revenue, above-threshold count, revenue-by-segment bar chart, monthly_revenue month/revenue line chart. Add a customer segment slicer. Label the monthly chart as full-dataset: it is intentionally unaffected by that slicer.
2. **Retention**: matrix with cohort rows, month columns, retention values formatted as percentages; conditional background formatting from 0 to 1. Missing cells must remain blank.
3. **Customer review**: customer_id, segment, cluster, recency, frequency, monetary, risk, evaluation_split; sort risk descending.

Place a visible notice on every page: “Synthetic portfolio data; inactivity is a 60-day proxy. No business impact or production accuracy is established.”

Save your edited report as a `.pbip` project or `.pbix` file. Refresh, verify totals against the CSVs, test slicers, and capture the actual report before representing the native dashboard as Desktop-verified.
