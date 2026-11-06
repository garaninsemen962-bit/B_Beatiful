"""Contract: compare(union_rows, params) -> iterable of comparison dictionaries.
RAW is canonical JSON: null, false, 0, empty string remain distinct.
"""
def compare(union_rows, params):
    pairs = {}
    for r in union_rows:
        key = (r['APP_ID'], r['PATH'], r['ARRAY_KEY'])
        pair = pairs.setdefault(key, {})
        if r['SOURCE'] in pair: raise ValueError('Duplicate attribute: ' + repr(key))
        pair[r['SOURCE']] = r
    for pair in pairs.values():
        b, a = pair.get('BEFORE'), pair.get('AFTER')
        base = a or b
        change = ('ATTRIBUTE_NEW' if b is None else 'ATTRIBUTE_LOST' if a is None
                  else 'SAME' if b['RAW'] == a['RAW'] else 'VALUE_CHANGED')
        row = {k:base[k] for k in ('APP_ID','PATH','ARRAY_KEY','PREFIX','CLASS','SUFFIX','KEY')}
        row.update(VALUE_BEFORE=b['VALUE'] if b else '', VALUE_AFTER=a['VALUE'] if a else '',
            ADJUSTED_BEFORE=b['VALUE_ADJUSTED'] if b else '', ADJUSTED_AFTER=a['VALUE_ADJUSTED'] if a else '',
            CHANGE_TYPE=change, DIFF_FLAG=int(change != 'SAME'))
        yield row
