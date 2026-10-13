"""CSV adapters only; Store owns validation, transactions and deduplication."""
import csv
import datetime as dt
import io
import re


def parse_csv(content, options=None):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 8_000_000:
        raise ValueError('CSV文件过大或格式不正确')
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip('\ufeff')), strict=True)
        headers = reader.fieldnames or []
        if not headers or len(headers) != len(set(headers)):
            raise ValueError('CSV表头缺失或重复')
        rows = list(reader)
    except csv.Error:
        raise ValueError('CSV引号或分隔符格式不正确') from None
    if len(rows) > 50000 or any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('CSV行数过多或列数与表头不一致')
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError('CSV选项应为对象')
    order = options.get('date_order', 'source')
    if order not in ('source', 'dmy', 'mdy'):
        raise ValueError('日期顺序应为来源默认、日/月/年或月/日/年')
    offset = options.get('utc_offset_minutes')
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int) or not -720 <= offset <= 840):
        raise ValueError('UTC时差应为 -720 至 840 的整数分钟')
    timezone = dt.timezone(dt.timedelta(minutes=offset)) if offset is not None else None
    if 'Start and stop time' in headers:
        required = {'Task', 'Description', 'Date', 'Start and stop time', 'Logged time', 'Project', 'Member', 'Email', 'Client', 'Billable', 'Tags'}
        if not required.issubset(headers):
            raise ValueError('请选择Toggl Focus时间日志CSV，表头缺少个人身份或精确起止字段')
        identities = {(row['Email'].strip().lower(), row['Member'].strip()) for row in rows}
        if any(not email and not user for email, user in identities) or len(identities) > 1:
            raise ValueError('CSV包含多个用户或缺少用户，请先筛选自己的记录再导出')
        records = []
        for index, row in enumerate(rows, 2):
            try:
                times = re.fullmatch(r'(\d{2}:\d{2}:\d{2}) - (\d{2}:\d{2}:\d{2})', row['Start and stop time'])
                if not times:
                    raise ValueError('缺少精确到秒的起止时间')
                date_format = '%d/%m/%Y' if order == 'dmy' else '%m/%d/%Y'
                start = dt.datetime.strptime(row['Date'] + ' ' + times[1], date_format + ' %H:%M:%S').replace(tzinfo=timezone)
                end = dt.datetime.strptime(row['Date'] + ' ' + times[2], date_format + ' %H:%M:%S').replace(tzinfo=timezone)
                if end < start:
                    end += dt.timedelta(days=1)
                start, end = start.timestamp(), end.timestamp()
                duration = row['Logged time']
                if not re.fullmatch(r'\d+:\d{2}:\d{2}', duration):
                    raise ValueError('缺少精确时长 Logged time')
                hours, minutes, seconds = map(int, duration.split(':'))
                if minutes >= 60 or seconds >= 60 or end - start != hours * 3600 + minutes * 60 + seconds or end <= start:
                    raise ValueError('起止时间与精确时长矛盾；超过一天的日志需要分段')
                if row['Billable'] not in ('Yes', 'No'):
                    raise ValueError('计费标记应为 Yes 或 No')
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
                raise ValueError(f'Toggl Focus CSV 第{index}行格式无效：{error}') from None
            def value(key):
                return '' if row[key] == '-' else row[key]
            note = row['Description']
            for key, label in (('Client', '客户'), ('Task', '任务')):
                if value(key):
                    note += '\n' + label + '：' + value(key)
            records.append({'start': start, 'end': end, 'app': 'Toggl Focus', 'category': '其他', 'source': 'manual',
                            'note': note, 'project': value('Project'), 'tags': [tag.strip() for tag in value('Tags').split(',') if tag.strip()],
                            'billable': row['Billable'] == 'Yes', 'hourly_rate': 0})
        return records
    if 'Start Date' in headers:
        required = {'Description', 'Project', 'Tags', 'Billable', 'User', 'Email', 'Start Date', 'Start Time', 'End Date', 'End Time', 'Duration (h)'}
        if not required.issubset(headers):
            raise ValueError('请选择Clockify详细CSV，表头缺少个人身份或精确起止字段')
        identities = {(row['Email'].strip().lower(), row['User'].strip()) for row in rows}
        if any(not email and not user for email, user in identities) or len(identities) > 1:
            raise ValueError('CSV包含多个用户或缺少用户，请先筛选自己的记录再导出')
        records = []
        for index, row in enumerate(rows, 2):
            try:
                for key in ('Start Time', 'End Time'):
                    if not re.fullmatch(r'\d{2}:\d{2}:\d{2}', row[key]):
                        raise ValueError('缺少精确到秒的起止时间')
                date_format = '%m/%d/%Y' if order == 'mdy' else '%d/%m/%Y'
                start = dt.datetime.strptime(row['Start Date'] + ' ' + row['Start Time'], date_format + ' %H:%M:%S').replace(tzinfo=timezone).timestamp()
                end = dt.datetime.strptime(row['End Date'] + ' ' + row['End Time'], date_format + ' %H:%M:%S').replace(tzinfo=timezone).timestamp()
                duration = row['Duration (h)']
                if not re.fullmatch(r'\d+:\d{2}:\d{2}', duration):
                    raise ValueError('缺少精确时长 Duration (h)，不能使用四舍五入的小数时长')
                hours, minutes, seconds = map(int, duration.split(':'))
                if minutes >= 60 or seconds >= 60 or end - start != hours * 3600 + minutes * 60 + seconds or end <= start:
                    raise ValueError('起止时间与精确时长矛盾')
                if row['Billable'] not in ('Yes', 'No'):
                    raise ValueError('计费标记应为 Yes 或 No')
            except (KeyError, TypeError, ValueError, OverflowError, OSError) as error:
                raise ValueError(f'Clockify CSV 第{index}行格式无效：{error}') from None
            note = row['Description']
            for key, label in (('Client', '客户'), ('Task', '任务')):
                if row.get(key):
                    note += '\n' + label + '：' + row[key]
            records.append({'start': start, 'end': end, 'app': 'Clockify', 'category': '其他',
                            'source': 'manual', 'note': note, 'project': row.get('Project', ''),
                            'tags': [tag.strip() for tag in row.get('Tags', '').split(',') if tag.strip()],
                            'billable': row.get('Billable') == 'Yes', 'hourly_rate': 0})
        return records
    if not {'开始', '结束', '分类', '来源'}.issubset(headers):
        raise ValueError('CSV表头不支持，请选择DayTrace、Clockify详细或Toggl Focus时间日志CSV')
    records = []
    for row in rows:
        def cell(key, default=''):
            value = row.get(key, default) or default
            return value[1:] if isinstance(value, str) and len(value) > 1 and value[0] == "'" and value[1] in '=+-@\t\r' else value
        records.append({'start': cell('开始'), 'end': cell('结束'), 'app': cell('应用'), 'title': cell('窗口标题'), 'domain': cell('网站域名'),
                        'category': cell('分类', '其他'), 'source': cell('来源', 'manual'), 'note': cell('备注'), 'project': cell('项目'),
                        'tags': [value for value in cell('标签').split('|') if value], 'billable': cell('计费') in ('1', 'true', '是'), 'hourly_rate': cell('时薪', '0')})
    return records
