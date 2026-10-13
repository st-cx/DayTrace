import datetime as dt
import heapq
import math

COLORS = {'学习': '#b7ead4', '编程': '#6ab4f0', '工作': '#b394f4', '浏览器': '#f1d973',
          '娱乐': '#f4a073', '游戏': '#f47788', '休息': '#a3adbb', '生活': '#83b4b0',
          '离开电脑': '#65717e', '其他': '#8593a3'}


def valid_date(text):
    return dt.date.fromisoformat(text or dt.date.today().isoformat()).isoformat()


def day_bounds(text):
    day = dt.date.fromisoformat(text)
    try:
        return (dt.datetime.combine(day, dt.time.min).timestamp(),
                dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min).timestamp())
    except (OSError, OverflowError):
        raise ValueError('日期超出本机支持的范围') from None


def date_range(first, last):
    first, last = valid_date(first), valid_date(last or first)
    if last < first or (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days > 365:
        raise ValueError('结束日期不能早于开始日期，范围最多366天')
    return first, last, day_bounds(first)[0], day_bounds(last)[1]


def overlaps(record, a, b):
    return max(0, min(record['end'], b) - max(record['start'], a))


def reconcile(records, a, b):
    """Sweep boundaries in O(n log n); manual > timer > active > idle.

    Half-open intervals prevent boundary overlaps. Ranking matches old manual
    tie-breaking (latest start, then ID) so migrations keep historical totals.
    """
    events = {}
    for index, record in enumerate(records):
        start, end = max(a, float(record['start'])), min(b, float(record['end']))
        if not math.isfinite(start) or not math.isfinite(end) or end <= start:
            continue
        rank = (3 if record['source'] == 'manual' else 2 if record['source'] == 'timer'
                else 0 if record['category'] == '离开电脑' else 1)
        item = (-rank, -record['start'], -record['id'], index, record)
        events.setdefault(start, []).append((True, index, item))
        events.setdefault(end, []).append((False, index, item))
    active, heap, result = set(), [], []
    points = sorted(events)
    for position, left in enumerate(points[:-1]):
        for added, index, item in events[left]:
            if added:
                active.add(index)
                heapq.heappush(heap, item)
            else:
                active.discard(index)
        while heap and heap[0][3] not in active:
            heapq.heappop(heap)
        right = points[position + 1]
        if not heap or right <= left:
            continue
        chosen = heap[0][4]
        if result and result[-1]['id'] == chosen['id'] and result[-1]['end'] == left:
            result[-1]['end'] = right
        else:
            result.append({**chosen, 'start': left, 'end': right})
    return result


def summarize(timeline):
    output = {'total': 0, 'totals': {}, 'projects': {}, 'apps': {}, 'domains': {},
              'productive_seconds': 0, 'billable_seconds': 0, 'estimated_amount': 0}
    for row in timeline:
        seconds = row['end'] - row['start']
        output['total'] += seconds
        for field, key in [('category', 'totals'), ('project', 'projects'), ('app', 'apps'), ('domain', 'domains')]:
            value = row.get(field, '')
            if value:
                output[key][value] = output[key].get(value, 0) + seconds
        if row['category'] in ('学习', '编程', '工作'):
            output['productive_seconds'] += seconds
        if row.get('billable'):
            output['billable_seconds'] += seconds
            output['estimated_amount'] += seconds / 3600 * row.get('hourly_rate', 0)
    output['estimated_amount'] = round(output['estimated_amount'], 2)
    return output
