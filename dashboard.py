import json
from pathlib import Path
import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title='Customer Intelligence', page_icon='◈', layout='wide')
st.markdown('''<style>.stApp{background:#f6f8fc}h1,h2,h3{color:#172b4d}
[data-testid="stMetric"]{background:white;padding:20px;border:1px solid #e0e7f0;border-radius:12px}
.block-container{padding-top:2rem}</style>''', unsafe_allow_html=True)
st.caption('CUSTOMER INTELLIGENCE / RETENTION WORKSPACE')
st.title('Understand the customers behind the numbers.')
folder = Path(__file__).parent / 'artifacts'
if not (folder / 'metrics.json').exists():
    st.info('Generate the sample dataset first: python -m analytics.pipeline')
    st.stop()
report = json.loads((folder / 'metrics.json').read_text())
customers = pd.read_csv(folder / 'customers.csv')
revenue = pd.read_csv(folder / 'monthly_revenue.csv')
retention = pd.read_csv(folder / 'retention.csv')
st.info(f"Data: {report['data_source']}. Scores estimate 60-day purchasing inactivity, not permanent customer loss.")
selected = st.sidebar.multiselect('Customer segments', sorted(customers.segment.unique()), default=sorted(customers.segment.unique()))
cutoff = st.sidebar.slider('Risk threshold', 0.0, 1.0, float(report['threshold']), .01)
st.sidebar.caption('Changing this exploration threshold does not change the locked test metrics.')
filtered = customers[customers.segment.isin(selected)]
cols = st.columns(4)
cols[0].metric('Customers in selection', f'{len(filtered):,}')
cols[1].metric('Observed revenue at snapshot', f'${filtered.monetary.sum():,.0f}')
cols[2].metric('Above selected risk threshold', f'{(filtered.risk >= cutoff).sum():,}')
cols[3].metric('Average order value', f'${filtered.monetary.sum() / max(filtered.frequency.sum(), 1):,.2f}')
overview, segments, model, explorer = st.tabs(['Business overview', 'Segments & retention', 'Model evaluation', 'Customer explorer'])
with overview:
    st.subheader('Monthly revenue')
    st.caption('Full transaction dataset, independent of the customer segment filter. Includes months after the prediction snapshot.')
    st.plotly_chart(px.area(revenue, x='month', y='revenue', color_discrete_sequence=['#4169e1']), use_container_width=True)
    left, right = st.columns(2)
    left.plotly_chart(px.histogram(filtered, x='risk', nbins=20, title='Risk distribution', color_discrete_sequence=['#11a08b']), use_container_width=True)
    right.plotly_chart(px.bar(filtered.groupby('segment').monetary.sum().reset_index(), x='segment', y='monetary', title='Revenue by segment'), use_container_width=True)
with segments:
    st.subheader('RFM segments and behavioral clusters')
    st.caption('Named segments use documented business rules. Cluster IDs come from K-means; they are not risk rankings.')
    st.plotly_chart(px.scatter(filtered, x='recency', y='frequency', color='segment', size='monetary', hover_data=['customer_id', 'cluster'], size_max=35), use_container_width=True)
    st.subheader('Observed monthly cohort retention')
    st.caption('Blank cells are unobserved months, not zero retention. Each row uses the original cohort size as denominator.')
    pivot = retention.pivot(index='cohort', columns='month', values='retention')
    st.plotly_chart(px.imshow(pivot, aspect='auto', zmin=0, zmax=1, color_continuous_scale='Blues'), use_container_width=True)
with model:
    st.subheader('Held-out evaluation')
    st.caption('Threshold selected on validation customers; metrics measured on different customers at a later cutoff.')
    st.dataframe(pd.DataFrame([report['model'], report['baseline']], index=['Logistic regression', 'Prior baseline']).drop(columns='confusion_matrix'), use_container_width=True)
    st.json({'split': report['split'], 'threshold': report['threshold'], 'test_prevalence': report['test_prevalence']})
    st.plotly_chart(px.imshow(report['model']['confusion_matrix'], x=['Predicted active', 'Predicted inactive'], y=['Observed active', 'Observed inactive'], text_auto=True, color_continuous_scale='Blues'), use_container_width=True)
    st.warning('Synthetic results demonstrate the evaluation workflow. They do not establish performance on a real business, causal effects, or financial lift.')
with explorer:
    st.subheader('Retention review queue')
    st.caption('Suggested review order only. Validate consent, contact policy, and customer context before outreach.')
    table = filtered.sort_values('risk', ascending=False)
    st.dataframe(table, use_container_width=True, hide_index=True)
    st.download_button('Download filtered customers', table.to_csv(index=False), 'customers.csv', 'text/csv')
