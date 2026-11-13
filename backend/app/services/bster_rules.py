# -*- coding: utf-8 -*-
import calendar
from datetime import date, datetime
from typing import Optional, Tuple

OPENING_CUTOFF_DATE = date(2024, 1, 1)

# 银行账号尾号 -> (公司代码, 科目编码, 科目名称)
BANK_ACCOUNT_MAP = {
    "7103": ("bster", "100209", "银行存款_农商行城北支行7103"),
    "6321": ("bster", "100215", "银行存款_农商行6321"),
    "1502": ("bster", "100211", "银行存款_宁波银行1502"),
    "1446": ("bster", "100212", "银行存款_宁波银行1446"),
    "494642": ("yt", "100201", "银行存款_宁波银行494642"),
    "98005": ("yt", "100211", "银行存款_内部账户98005"),
}

# 贷款/归集还款账号（非银行存款）
LOAN_ACCOUNTS = {
    "67859": ("230102", "长期借款_银行贷款（农商行城北支行）"),
    "077006": ("230103", "长期借款_银行贷款（宁波银行）"),
}

# 2024+ 关键词分类矩阵 -> (科目编码, 业务标签, 是否红字负数)
RULES_2024 = [
    (["黄旭", "WONGKING"], "218108", "SHAREHOLDER_CURRENT", False),
    (["入息", "结息", "利息收入"], "550302", "BANK_INTEREST_RED", True),
    (["手续费", "短信收费", "网银服务费", "账户管理费", "年费", "工本费"], "550301", "BANK_FEE", False),
    (["归还利息", "贷款利息", "利息支出"], "550303", "LOAN_INTEREST", False),
    (["归还贷款", "还本金", "贷款本金", "贷款批扣"], "230102", "LOAN_PRINCIPAL", False),
    (["工资", "年终奖", "代发"], "2151", "SALARY_PAYOUT", False),
    (["公积金", "住房公积金"], "550212", "HOUSING_FUND", False),
    (["社保", "社会保险"], "550213", "SOCIAL_SECURITY", False),
    (["税费扣缴", "暂收款", "税务"], "217115", "TAX_WITHHOLDING", False),
    (["光伏发电", "分布式光伏"], "510105", "SOLAR_REVENUE", False),
    (["停车费", "神州路通"], "510203", "PARKING_REVENUE", False),
    (["维保费", "修缮", "装修", "门窗", "维修"], "550220", "MAINTENANCE_EXP", False),
    (["垃圾清运", "佰鑫信息"], "540106", "GARBAGE_COST", False),
    (["财产保险", "太平财产"], "550210", "INSURANCE_EXP", False),
    (["代理记账", "办公", "检测费", "刻字"], "550203", "OFFICE_EXP", False),
    (["差旅", "机票", "高铁", "住宿", "停车费报销"], "550204", "TRAVEL_EXP", False),
    (["电费", "供电"], "113213", "PREPAID_POWER", False),
    (["水费", "自来水"], "113214", "PREPAID_WATER", False),
]

def is_valid_2024_plus(tx_date: date) -> bool:
    """硬性门禁：仅处理 2024-01-01 及以后的账目"""
    return tx_date >= OPENING_CUTOFF_DATE

def to_month_end_date(tx_date: date) -> date:
    """2024+ 凭证日期统一归集为交易所在月的最后一天"""
    last_day = calendar.monthrange(tx_date.year, tx_date.month)[1]
    return date(tx_date.year, tx_date.month, last_day)

def format_summary(tx_date: date, biz_label: str, counterparty: str = "", invoice_no: str = "") -> str:
    """标准摘要：MM/DD业务描述：对方户名#票据号"""
    prefix = tx_date.strftime("%m/%d")
    core = f"{prefix}{biz_label}"
    if counterparty:
        core += f"：{counterparty}"
    if invoice_no:
        core += f"#{invoice_no}"
    return core
