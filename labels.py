# -*- coding: utf-8 -*-
"""Понятные пользователю названия атрибутов ответа СПР и полей витрины.

Технический путь (PATH) остаётся ключом фильтров и ссылок; здесь только подписи.
Для известных путей задано готовое название, для остальных оно собирается
из перевода отдельных сегментов, а нераспознанные сегменты остаются как есть.
"""

FIELDS = {
    'PATH': 'Атрибут',
    'CHANGE_TYPE': 'Тип изменения',
    'ARRAY_KEY': 'Элемент массива',
    'VALUE_ADJUSTED': 'Значение (бакет)',
    'PREFIX': 'Блок ответа',
    'CLASS': 'Массив',
    'SUFFIX': 'Вложенный блок',
    'KEY': 'Поле',
    'SOURCE': 'Срез',
    'DIFF_FLAG': 'Есть различие',
}

VALUES = {
    'CHANGE_TYPE': {'VALUE_CHANGED': 'Значение изменилось', 'ATTRIBUTE_NEW': 'Атрибут появился',
                    'ATTRIBUTE_LOST': 'Атрибут исчез', 'SAME': 'Без изменений'},
    'SOURCE': {'BEFORE': 'BEFORE (прод)', 'AFTER': 'AFTER (бета)'},
    'DIFF_FLAG': {'1': 'Да', '0': 'Нет'},
}

PATHS = {
    # Блоки ответа (используются в проверке «Различия есть, решение то же»)
    'decisionResult.product_trials.scoring.pd': 'Скоринг продукта · PD',
    'decisionResult.product_trials.scoring.scoreModels': 'Скоринг продукта · модели',
    'decisionResult.product_trials.scoring': 'Скоринг продукта',
    'decisionResult.product_trials.cutOff': 'Решение по продукту · порог отсечения',
    'decisionResult.product_trials.rules': 'Стоп-факторы',
    'decisionResult.product_trials': 'Решение по продукту',
    'decisionResult.available_offers': 'Доступные офферы',
    'decisionResult.scoring.features': 'Признаки скоринга',
    'decisionResult.scoring': 'Общий скоринг',
    'ext_data.body.params': 'Внешний запрос · параметры',
    'ext_data.body': 'Внешний запрос',
    'ext_data.characteristics': 'Внешние данные · характеристики',
    'appData.application_id': 'Заявка · идентификатор',
    'characteristics.score_equ': 'Характеристики клиента · скор Эквифакс',
    'characteristics.score_okb': 'Характеристики клиента · скор ОКБ',
    'decisionResult.decision': 'Итоговое решение',
    'decisionResult.available_offers.product_id': 'Доступные офферы · ID продукта',
    'decisionResult.available_offers.product_name': 'Доступные офферы · продукт',
    'decisionResult.available_offers.rank': 'Доступные офферы · место в выдаче',
    'decisionResult.available_offers.type': 'Доступные офферы · тип оффера',
    'decisionResult.product_trials.calculationType': 'Решение по продукту · тип расчёта',
    'decisionResult.product_trials.cutOff.name': 'Решение по продукту · порог отсечения (название)',
    'decisionResult.product_trials.cutOff.value': 'Решение по продукту · порог отсечения (значение)',
    'decisionResult.product_trials.decision': 'Решение по продукту',
    'decisionResult.product_trials.epr': 'Решение по продукту · EPR',
    'decisionResult.product_trials.partner_id': 'Решение по продукту · ID партнёра',
    'decisionResult.product_trials.partner_name': 'Решение по продукту · партнёр',
    'decisionResult.product_trials.partner_type': 'Решение по продукту · тип партнёра',
    'decisionResult.product_trials.product_id': 'Решение по продукту · ID продукта',
    'decisionResult.product_trials.product_name': 'Решение по продукту · продукт',
    'decisionResult.product_trials.rejectReason': 'Решение по продукту · причина отказа',
    'decisionResult.product_trials.rejectReasonCode': 'Решение по продукту · код причины отказа',
    'decisionResult.product_trials.routing_method': 'Решение по продукту · способ маршрутизации',
    'decisionResult.product_trials.rules.description': 'Стоп-факторы · описание правила',
    'decisionResult.product_trials.rules.hit': 'Стоп-факторы · сработало ли правило',
    'decisionResult.product_trials.rules.name': 'Стоп-факторы · правило',
    'decisionResult.product_trials.scoring.pd.modelName': 'Скоринг продукта · целевая модель PD',
    'decisionResult.product_trials.scoring.pd.value': 'Скоринг продукта · балл PD',
    'decisionResult.product_trials.scoring.scoreModels.modelName': 'Скоринг продукта · модель',
    'decisionResult.product_trials.scoring.scoreModels.type': 'Скоринг продукта · тип модели',
    'decisionResult.product_trials.scoring.scoreModels.value': 'Скоринг продукта · балл модели',
    'decisionResult.product_trials.tariff': 'Решение по продукту · тариф',
    'decisionResult.scoring.features.age': 'Признаки скоринга · возраст',
    'decisionResult.scoring.features.cntApprovedContracts3y': 'Признаки скоринга · одобренных договоров за 3 года',
    'decisionResult.scoring.features.cntDelinq90d12m': 'Признаки скоринга · просрочек 90+ за 12 мес.',
    'decisionResult.scoring.features.monthsSinceLastLoan': 'Признаки скоринга · месяцев с последнего займа',
    'decisionResult.scoring.features.score_okb': 'Признаки скоринга · скор ОКБ',
    'decisionResult.scoring.features.sumActiveDeals': 'Признаки скоринга · сумма активных сделок',
    'decisionResult.scoring.modelName': 'Общий скоринг · модель',
    'decisionResult.scoring.value': 'Общий скоринг · балл',
    'ext_data.body.action': 'Внешний запрос · действие',
    'ext_data.body.id': 'Внешний запрос · идентификатор',
    'ext_data.body.params.data': 'Внешний запрос · данные ответа',
    'ext_data.body.params.error': 'Внешний запрос · ошибка',
    'ext_data.body.params.ok': 'Внешний запрос · успешно',
    'ext_data.characteristics.score_equ': 'Внешние данные · скор Эквифакс',
    'ext_data.characteristics.score_okb': 'Внешние данные · скор ОКБ',
    'ext_data.status': 'Внешний запрос · статус',
    'product_catalog.is_new_mfo': 'Каталог продуктов · новое МФО',
    'product_catalog.mfo_id': 'Каталог продуктов · ID МФО',
    'product_catalog.product_id': 'Каталог продуктов · ID продукта',
    'product_catalog.routing_method': 'Каталог продуктов · способ маршрутизации',
    'tariff_matrix.mfo_id': 'Тарифная матрица · ID МФО',
    'tariff_matrix.product_id': 'Тарифная матрица · ID продукта',
    'tariff_matrix.tariff': 'Тарифная матрица · тариф',
}

SEGMENTS = {
    'appData': 'Заявка', 'decisionResult': 'Решение', 'characteristics': 'Характеристики клиента',
    'ext_data': 'Внешние данные', 'product_catalog': 'Каталог продуктов', 'tariff_matrix': 'Тарифная матрица',
    'available_offers': 'Доступные офферы', 'product_trials': 'Решение по продукту', 'rules': 'Стоп-факторы',
    'scoring': 'Скоринг', 'scoreModels': 'Модели', 'features': 'Признаки', 'cutOff': 'Порог отсечения',
    'pd': 'PD', 'body': 'Запрос', 'params': 'Параметры',
    'decision': 'решение', 'value': 'значение', 'name': 'название', 'modelName': 'модель', 'type': 'тип',
    'status': 'статус', 'product_id': 'ID продукта', 'product_name': 'продукт', 'partner_id': 'ID партнёра',
    'partner_name': 'партнёр', 'tariff': 'тариф', 'rank': 'место', 'hit': 'сработало', 'description': 'описание',
}


def path(value):
    """Название атрибута для пользователя; технический путь возвращается, если перевести нечего."""
    if not value:
        return value
    if value in PATHS:
        return PATHS[value]
    parts = value.split('.')
    translated = [SEGMENTS.get(x, x) for x in parts]
    if translated == parts:
        return value
    head = [x for x in translated[:-1] if x]
    return ' · '.join(head[:2] + [translated[-1]]) if head else translated[-1]


def field(name):
    return FIELDS.get(name, name)


def value(field_name, raw):
    if field_name == 'PATH':
        return path(raw)
    return VALUES.get(field_name, {}).get(str(raw), raw)


DECISIONS = {'APPROVE': 'Одобрено', 'REJECT': 'Отказ', 'REVIEW': 'На проверку', 'MANUAL': 'На проверку'}
CONFIDENCE = {'HIGH': '', 'LOW': 'мало данных'}


def decision(raw):
    return DECISIONS.get(raw, raw)


def source(raw):
    return VALUES['SOURCE'].get(raw, raw)


def looks_like_path(text):
    return isinstance(text, str) and '.' in text and ' ' not in text and text.split('.')[0] in SEGMENTS


def product(pid, names):
    name = names.get(str(pid))
    return f'{name} ({pid})' if name else str(pid)


def array_key(raw, names):
    """'15210|AGELOW@21' -> 'KUPI_KUPI (15210) · AGELOW@21'."""
    if not raw:
        return '—'
    import re
    parts = [x for x in re.split(r'[|,]', raw) if x != '']
    if not parts:
        return raw
    return ' · '.join([product(parts[0], names) if parts[0] in names else parts[0]] + parts[1:])


def products(raw, names, sep=';'):
    return ', '.join(names.get(x, x) for x in (raw or '').split(sep) if x)


def percent(metric):
    try:
        return (f'{float(metric) * 100:.1f}'.replace('.', ',')) + '\u00a0%'
    except (TypeError, ValueError):
        return str(metric)


def plural(n, one, few, many):
    n = abs(int(n)) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    return one if n == 1 else few if 2 <= n <= 4 else many


CHECK_LABELS = {
    'Approval Rate по продукту упал': 'Доля одобрений по продукту упала',
    'Ошибки исполнения BEFORE': 'Ошибки исполнения BEFORE (прод)',
    'Ошибки исполнения AFTER': 'Ошибки исполнения AFTER (бета)',
    'Изменился состав атрибутов': 'Изменился состав атрибутов',
    'Различие вызвано входными данными': 'Различие вызвано входными данными',
}


def check(label):
    return CHECK_LABELS.get(label, label)


ROLES = {'owner': 'Владелец', 'editor': 'Редактор', 'reader': 'Читатель'}
VISIBILITY = {'private': 'Только владелец', 'named': 'Поимённый доступ', 'public': 'Все пользователи'}
STATES = {'queued': 'В очереди', 'running': 'Выполняется', 'done': 'Готово', 'failed': 'Ошибка',
          'cancelled': 'Отменено', 'deleted': 'Удалено'}
USER_STATUS = {'pending': 'Ожидает подтверждения', 'active': 'Активен', 'rejected': 'Отклонён', 'blocked': 'Заблокирован',
               'deleted': 'Удалён'}
DATASET_FIELDS = {'apps': 'Заявок', 'files': 'Файлов', 'sha256': 'Контрольная сумма SHA-256', 'slices': 'Ответов по срезам',
                  'uncompressed_bytes': 'Объём по срезам, байт', 'invalid_responses': 'Повреждённых ответов'}
DATASET_VALUES = {'bundled-open-dataset': 'встроенный открытый датасет'}


def role(raw):
    return ROLES.get(raw, raw)


def visibility(raw):
    return VISIBILITY.get(raw, raw)


def state(raw):
    return STATES.get(raw, raw)


def user_status(raw):
    return USER_STATUS.get(raw, raw)


def transition(text):
    """'REJECT → APPROVE' -> 'Отказ → Одобрено'."""
    return ' → '.join(decision(x.strip()) for x in str(text).split('→'))


def dataset_value(key, raw):
    if isinstance(raw, dict):
        return ', '.join(f'{source(k) if k in ("BEFORE", "AFTER") else k}: {v}' for k, v in raw.items())
    return DATASET_VALUES.get(raw, raw)
