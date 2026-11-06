"""Contract: normalize(document, app_id, source, params) -> list[dict].
segments(before, after, app_id, params) -> dict. Array paths omit [].
Edit this procedure, not the application, to support another response format.
"""
import json

DEFAULTS = {
    'exclude': ['appData.request_id', 'appData.createDateTime'],
    'arrays': {
        'decisionResult.available_offers': ['product_id', 'type'],
        'decisionResult.product_trials': ['product_id'],
        'decisionResult.product_trials.rules': ['name'],
        'decisionResult.product_trials.scoring.scoreModels': ['modelName'],
        'decisionResult.scoring': ['modelName'],
        'decisionResult.scoring.value': 'index',
        'ext_data': ['body.action'],
        'product_catalog': ['mfo_id', 'product_id'],
        'tariff_matrix': ['mfo_id', 'product_id'],
    },
}

def get(obj, path, default=None):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj

def scalar(v):
    if v is None: return 'NULL'
    if isinstance(v, str): return v
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

def bucket(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 0 <= v <= 1:
            return '[0;0.3]' if v <= .3 else '(0.3;0.6]' if v <= .6 else '(0.6;1]'
        if v >= 1000:
            for edge in (10000, 50000, 100000, 250000, 1000000):
                if v <= edge: return '<= ' + str(edge)
            return '> 1000000'
    return scalar(v)

def normalize(document, app_id, source, params):
    cfg = {**DEFAULTS, **params}
    modes = {**DEFAULTS['arrays'], **params.get('arrays', {})}
    excluded = cfg['exclude']; rows = []; seen = set()
    def walk(v, path, keys):
        if any(path == x or path.startswith(x + '.') for x in excluded): return
        if isinstance(v, dict):
            for k, val in v.items(): walk(val, path + '.' + k if path else k, keys)
        elif isinstance(v, list):
            mode = modes.get(path)
            if mode is None:
                raise ValueError('Укажите режим массива в params.arrays: ' + path)
            local = set()
            for i, val in enumerate(v):
                if mode == 'index': key = str(i)
                elif mode == 'value': key = scalar(val)
                else:
                    values = [get(val, k, '__MISSING__') for k in mode]
                    if '__MISSING__' in values: raise ValueError('Нет ключа массива: ' + path)
                    key = ','.join(scalar(x).replace('%', '%25').replace('|', '%7C').replace(',', '%2C') for x in values)
                if key in local: raise ValueError('Повтор ключа массива: ' + path + ':' + key)
                local.add(key); walk(val, path, keys + [key])
        else:
            array_key = '|'.join(keys); identity = (path, array_key)
            if identity in seen: raise ValueError('Повтор атрибута: ' + repr(identity))
            seen.add(identity); p = path.split('.')
            rows.append(dict(APP_ID=app_id, SOURCE=source, PATH=path, ARRAY_KEY=array_key,
                PREFIX=p[0] if len(p)>1 else '', CLASS=p[1] if len(p)>2 else '',
                SUFFIX='.'.join(p[2:-1]), KEY=p[-1], VALUE=scalar(v),
                RAW=json.dumps(v, ensure_ascii=False, sort_keys=True, allow_nan=False),
                VALUE_ADJUSTED=bucket(v)))
    walk(document, '', [])
    return rows

def segments(before, after, app_id, params):
    # Per-product maps preserve the association when there is more than one product.
    def trials(doc):
        return {str(x['product_id']): x for x in get(doc, 'decisionResult.product_trials', [])}
    b, a = trials(before), trials(after)
    products = sorted(set(b) | set(a))
    def field(doc, path): return scalar(get(doc, path))
    def mapped(ts, path):
        values = {k: get(v, path) for k, v in sorted(ts.items())}
        unique = {scalar(x) for x in values.values()}
        return next(iter(unique)) if len(unique) == 1 else json.dumps(values, ensure_ascii=False, sort_keys=True)
    def top(doc):
        offers = get(doc, 'decisionResult.available_offers', [])
        return str(min(offers, key=lambda x:x.get('rank', 999999))['product_id']) if offers else ''
    s = dict(APP_ID=app_id, PRODUCTS=';'.join(products),
        DECISION_BEFORE=field(before, 'decisionResult.decision'), DECISION_AFTER=field(after, 'decisionResult.decision'),
        TOP_OFFER_BEFORE=top(before), TOP_OFFER_AFTER=top(after),
        PARTNER_TYPE=';'.join(sorted({str(x.get('partner_type', '')) for x in [*b.values(), *a.values()]})),
        ROUTING_METHOD=';'.join(sorted({str(x.get('routing_method', '')) for x in [*b.values(), *a.values()]})))
    s['DECISION_CHANGED'] = int(s['DECISION_BEFORE'] != s['DECISION_AFTER'])
    for suffix, ts, doc in [('BEFORE', b, before), ('AFTER', a, after)]:
        for name, path in [('PD_MODEL','scoring.pd.modelName'), ('CUTOFF','cutOff.name'), ('REJECT_REASON','rejectReason')]:
            s[name+'_'+suffix] = mapped(ts, path)
        s['SCORE_OKB_'+suffix] = field(doc, 'characteristics.score_okb')
    s['PRODUCT_DETAILS'] = {'BEFORE':b, 'AFTER':a}
    return s
