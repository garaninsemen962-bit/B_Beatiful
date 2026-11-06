"""Contract: aggregate(rows, segments, params) -> iterable of facts.
CNT is distinct applications WITHIN a group; don't sum distinct counts across attributes.
"""
def aggregate(rows, segments, params):
    groups = {}
    for r in rows:
        for source in ('BEFORE','AFTER'):
            if source == 'BEFORE' and r['CHANGE_TYPE'] == 'ATTRIBUTE_NEW': continue
            if source == 'AFTER' and r['CHANGE_TYPE'] == 'ATTRIBUTE_LOST': continue
            key = tuple(r[k] for k in ('PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','PATH','DIFF_FLAG','CHANGE_TYPE')) + (source, r['ADJUSTED_'+source])
            groups.setdefault(key, set()).add(r['APP_ID'])
    fields = ('PREFIX','CLASS','SUFFIX','ARRAY_KEY','KEY','PATH','DIFF_FLAG','CHANGE_TYPE','SOURCE','VALUE_ADJUSTED')
    for key, apps in groups.items():
        yield dict(zip(fields,key), CNT=len(apps), APP_IDS=sorted(apps))
