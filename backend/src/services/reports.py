import time, os, csv, io, hashlib, json
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.config.settings import ARTIFACT_DIR
def create(db, data):
    j=db.get(Job,data.job_id)
    if not j or j.status!='completed' or not j.approved:
        raise HTTPException(409,'请先选择已完成且获批准的分析结果')
    r=Report(name=data.name, job_id=j.id, destination=data.destination, interval_seconds=data.interval_seconds, next_run=time.time()+data.delay_seconds, enabled=True)
    db.add(r); db.flush()
    return r
DETAIL_COLUMNS=[
    ('record_type','记录类型'),('channel','播出链路'),
    ('start','起始日'),('end','结束日'),('days','持续天数'),('direction','方向'),
    ('peak_deviation','峰值偏离'),('peak_day','峰值日'),
    ('day','播出日'),('actual','实测'),('ewma','EWMA'),
    ('lower','下控制限'),('upper','上控制限'),('status','简报标记'),
    ('note','说明'),
]
def detail_rows(result):
    """Flatten events, anomaly days and failed channels into one reconcilable list.

    Each row carries a record type so the file can be filtered back to the
    event total and the complete anomaly list without truncation.
    """
    rows=[]
    for event in result.get('events') or []:
        rows.append({'record_type':'事件',**{key:event.get(key,'') for key in ('channel','start','end','days','direction','peak_deviation','peak_day')}})
    anomaly_rows=(result.get('anomaly_table') or {}).get('rows')
    if anomaly_rows is None:
        # Legacy single-channel results stored the (truncated) anomaly list in
        # 'table'; still emit every row that exists so the file stays complete.
        legacy=(result.get('table') or {}).get('rows') or []
        anomaly_rows=[row for row in legacy if row.get('day')]
    for row in anomaly_rows or []:
        rows.append({'record_type':'越限日',**{key:row.get(key,'') for key in ('channel','day','actual','ewma','lower','upper','status')}})
    for failure in result.get('failures') or []:
        rows.append({'record_type':'失败链路','channel':failure.get('channel',''),'note':failure.get('reason','')})
    return rows
def deliver(db, report):
    j=db.get(Job,report.job_id)
    if not j or not j.approved: return
    out=io.StringIO()
    out.write('﻿')
    rows=detail_rows(j.result)
    writer=csv.writer(out)
    def safe(v):
        if isinstance(v,(int,float)) and not isinstance(v,bool): return v
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    writer.writerow([safe(label) for _,label in DETAIL_COLUMNS])
    for row in rows: writer.writerow([safe(row.get(key,'')) for key,_ in DETAIL_COLUMNS])
    data=out.getvalue().encode('utf-8')
    delivery=Delivery(report_id=report.id, created_at=time.time(), checksum=hashlib.sha256(data).hexdigest(), filename='', destination=report.destination)
    db.add(delivery); db.flush()
    delivery.filename=f'report-{report.id}-delivery-{delivery.id}.csv'
    os.makedirs(ARTIFACT_DIR,exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename),'wb') as f:f.write(data)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename+'.json'),'w') as f: json.dump({'job':j.name,'params':j.params,'result':j.result,'counts':j.result.get('counts',{}),'detail_rows':len(rows),'checksum':delivery.checksum},f,ensure_ascii=False,allow_nan=False)
    report.next_run=time.time()+report.interval_seconds
    return delivery
