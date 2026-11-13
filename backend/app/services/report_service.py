# -*- coding: utf-8 -*-
import io
from decimal import Decimal
from typing import List, Dict, Any
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AccountSet, ChartOfAccount, Voucher, VoucherEntry, AccountBalance


async def _load_coa_and_balances(db: AsyncSession, account_set_id: str, period: str):
    coa_rows = (
        await db.execute(
            select(ChartOfAccount)
            .where(ChartOfAccount.account_set_id == account_set_id)
            .order_by(ChartOfAccount.code)
        )
    ).scalars().all()

    year_prefix = period[:4]
    jan_period = f"{year_prefix}-01"

    # 当期余额表
    cur_bals = (
        await db.execute(
            select(AccountBalance).where(
                AccountBalance.account_set_id == account_set_id,
                AccountBalance.period == period,
            )
        )
    ).scalars().all()
    cur_map = {b.account_code: b for b in cur_bals}

    # 年初余额（当年 01 期期初）
    jan_bals = (
        await db.execute(
            select(AccountBalance).where(
                AccountBalance.account_set_id == account_set_id,
                AccountBalance.period == jan_period,
            )
        )
    ).scalars().all()
    jan_map = {b.account_code: b for b in jan_bals}

    # 本年累计发生额（YYYY-01 ~ period）
    ytd_bals = (
        await db.execute(
            select(AccountBalance).where(
                AccountBalance.account_set_id == account_set_id,
                AccountBalance.period >= jan_period,
                AccountBalance.period <= period,
            )
        )
    ).scalars().all()
    ytd_dr: Dict[str, float] = {}
    ytd_cr: Dict[str, float] = {}
    for b in ytd_bals:
        ytd_dr[b.account_code] = round(ytd_dr.get(b.account_code, 0.0) + float(b.period_debit), 2)
        ytd_cr[b.account_code] = round(ytd_cr.get(b.account_code, 0.0) + float(b.period_credit), 2)

    return coa_rows, cur_map, jan_map, ytd_dr, ytd_cr


def _leaf_metrics(coa_rows, cur_map, jan_map, ytd_dr, ytd_cr) -> Dict[str, Dict[str, float]]:
    """计算所有末级叶子科目的年初、期初、本期、本年累计及期末净额"""
    metrics: Dict[str, Dict[str, float]] = {}
    for c in coa_rows:
        if not c.is_leaf:
            continue
        b = cur_map.get(c.code)
        jb = jan_map.get(c.code)

        op_dr = float(b.opening_debit) if b else 0.0
        op_cr = float(b.opening_credit) if b else 0.0
        p_dr = float(b.period_debit) if b else 0.0
        p_cr = float(b.period_credit) if b else 0.0

        y_op_dr = float(jb.opening_debit) if jb else 0.0
        y_op_cr = float(jb.opening_credit) if jb else 0.0
        c_ytd_dr = ytd_dr.get(c.code, 0.0)
        c_ytd_cr = ytd_cr.get(c.code, 0.0)

        if c.balance_direction == "DEBIT":
            year_open = round(y_op_dr - y_op_cr, 2)
            period_open = round(op_dr - op_cr, 2)
            period_close = round(period_open + p_dr - p_cr, 2)
        else:
            year_open = round(y_op_cr - y_op_dr, 2)
            period_open = round(op_cr - op_dr, 2)
            period_close = round(period_open + p_cr - p_dr, 2)

        metrics[c.code] = {
            "year_open": year_open,
            "period_open": period_open,
            "period_dr": round(p_dr, 2),
            "period_cr": round(p_cr, 2),
            "ytd_dr": round(c_ytd_dr, 2),
            "ytd_cr": round(c_ytd_cr, 2),
            "period_close": period_close,
        }
    return metrics


def _sum_prefix(metrics: Dict[str, Dict[str, float]], prefixes: tuple[str, ...], field: str) -> float:
    return round(
        sum(v[field] for code, v in metrics.items() if code.startswith(prefixes)),
        2,
    )


async def build_category_summary(db: AsyncSession, account_set_id: str, period: str) -> Dict[str, Any]:
    """1. 分类表（一级科目汇总分类表 / 试算平衡表）"""
    coa_rows, cur_map, jan_map, ytd_dr, ytd_cr = await _load_coa_and_balances(db, account_set_id, period)
    metrics = _leaf_metrics(coa_rows, cur_map, jan_map, ytd_dr, ytd_cr)

    top_accounts = [c for c in coa_rows if len(c.code) == 4]
    rows = []
    tot_op_dr = tot_op_cr = tot_p_dr = tot_p_cr = tot_y_dr = tot_y_cr = tot_cl_dr = tot_cl_cr = 0.0

    for top in top_accounts:
        child_codes = [k for k in metrics.keys() if k.startswith(top.code)]
        if not child_codes:
            continue
        op_net = round(sum(metrics[k]["period_open"] for k in child_codes), 2)
        p_dr = round(sum(metrics[k]["period_dr"] for k in child_codes), 2)
        p_cr = round(sum(metrics[k]["period_cr"] for k in child_codes), 2)
        y_dr = round(sum(metrics[k]["ytd_dr"] for k in child_codes), 2)
        y_cr = round(sum(metrics[k]["ytd_cr"] for k in child_codes), 2)
        cl_net = round(sum(metrics[k]["period_close"] for k in child_codes), 2)

        if abs(op_net) < 0.005 and abs(p_dr) < 0.005 and abs(p_cr) < 0.005 and abs(cl_net) < 0.005:
            continue

        if top.balance_direction == "DEBIT":
            op_dr, op_cr = (op_net, 0.0) if op_net >= 0 else (0.0, abs(op_net))
            cl_dr, cl_cr = (cl_net, 0.0) if cl_net >= 0 else (0.0, abs(cl_net))
        else:
            op_cr, op_dr = (op_net, 0.0) if op_net >= 0 else (0.0, abs(op_net))
            cl_cr, cl_dr = (cl_net, 0.0) if cl_net >= 0 else (0.0, abs(cl_net))

        tot_op_dr = round(tot_op_dr + op_dr, 2)
        tot_op_cr = round(tot_op_cr + op_cr, 2)
        tot_p_dr = round(tot_p_dr + p_dr, 2)
        tot_p_cr = round(tot_p_cr + p_cr, 2)
        tot_y_dr = round(tot_y_dr + y_dr, 2)
        tot_y_cr = round(tot_y_cr + y_cr, 2)
        tot_cl_dr = round(tot_cl_dr + cl_dr, 2)
        tot_cl_cr = round(tot_cl_cr + cl_cr, 2)

        rows.append({
            "code": top.code,
            "name": top.name,
            "category": top.category,
            "opening_debit": op_dr,
            "opening_credit": op_cr,
            "period_debit": p_dr,
            "period_credit": p_cr,
            "ytd_debit": y_dr,
            "ytd_credit": y_cr,
            "closing_debit": cl_dr,
            "closing_credit": cl_cr,
        })

    return {
        "period": period,
        "rows": rows,
        "totals": {
            "opening_debit": tot_op_dr,
            "opening_credit": tot_op_cr,
            "period_debit": tot_p_dr,
            "period_credit": tot_p_cr,
            "ytd_debit": tot_y_dr,
            "ytd_credit": tot_y_cr,
            "closing_debit": tot_cl_dr,
            "closing_credit": tot_cl_cr,
            "is_balanced": abs(tot_cl_dr - tot_cl_cr) < 0.01,
        },
    }


async def build_income_statement(db: AsyncSession, account_set_id: str, period: str) -> Dict[str, Any]:
    """2. 损益表（利润表 - 《企业会计制度》2001 标准格式）"""
    coa_rows, cur_map, jan_map, ytd_dr, ytd_cr = await _load_coa_and_balances(db, account_set_id, period)
    metrics = _leaf_metrics(coa_rows, cur_map, jan_map, ytd_dr, ytd_cr)

    def rev(prefixes):
        m_val = round(sum(v["period_cr"] - v["period_dr"] for k, v in metrics.items() if k.startswith(prefixes)), 2)
        y_val = round(sum(v["ytd_cr"] - v["ytd_dr"] for k, v in metrics.items() if k.startswith(prefixes)), 2)
        return m_val, y_val

    def exp(prefixes):
        m_val = round(sum(v["period_dr"] - v["period_cr"] for k, v in metrics.items() if k.startswith(prefixes)), 2)
        y_val = round(sum(v["ytd_dr"] - v["ytd_cr"] for k, v in metrics.items() if k.startswith(prefixes)), 2)
        return m_val, y_val

    m_main_rev, y_main_rev = rev(("5101",))
    m_main_cost, y_main_cost = exp(("5401",))
    m_main_tax, y_main_tax = exp(("5402",))
    m_main_profit = round(m_main_rev - m_main_cost - m_main_tax, 2)
    y_main_profit = round(y_main_rev - y_main_cost - y_main_tax, 2)

    m_oth_rev, y_oth_rev = rev(("5102",))
    m_oth_cost, y_oth_cost = exp(("5405",))
    m_oth_profit = round(m_oth_rev - m_oth_cost, 2)
    y_oth_profit = round(y_oth_rev - y_oth_cost, 2)

    m_sell_exp, y_sell_exp = exp(("5501",))
    m_admin_exp, y_admin_exp = exp(("5502",))
    m_fin_exp, y_fin_exp = exp(("5503",))

    m_op_profit = round(m_main_profit + m_oth_profit - m_sell_exp - m_admin_exp - m_fin_exp, 2)
    y_op_profit = round(y_main_profit + y_oth_profit - y_sell_exp - y_admin_exp - y_fin_exp, 2)

    m_inv_inc, y_inv_inc = rev(("5201",))
    m_sub_inc, y_sub_inc = rev(("5203",))
    m_non_op_inc, y_non_op_inc = rev(("5301",))
    m_non_op_exp, y_non_op_exp = exp(("5601",))

    m_total_profit = round(m_op_profit + m_inv_inc + m_sub_inc + m_non_op_inc - m_non_op_exp, 2)
    y_total_profit = round(y_op_profit + y_inv_inc + y_sub_inc + y_non_op_inc - y_non_op_exp, 2)

    m_tax_exp, y_tax_exp = exp(("5701",))
    m_net_profit = round(m_total_profit - m_tax_exp, 2)
    y_net_profit = round(y_total_profit - y_tax_exp, 2)

    lines = [
        {"line_no": 1, "item": "一、主营业务收入", "month_amount": m_main_rev, "ytd_amount": y_main_rev, "is_bold": True},
        {"line_no": 2, "item": "  减：主营业务成本", "month_amount": m_main_cost, "ytd_amount": y_main_cost, "is_bold": False},
        {"line_no": 3, "item": "      主营业务税金及附加", "month_amount": m_main_tax, "ytd_amount": y_main_tax, "is_bold": False},
        {"line_no": 4, "item": "二、主营业务利润（亏损以“-”号填列）", "month_amount": m_main_profit, "ytd_amount": y_main_profit, "is_bold": True},
        {"line_no": 5, "item": "  加：其他业务利润（其他业务收入减支出）", "month_amount": m_oth_profit, "ytd_amount": y_oth_profit, "is_bold": False},
        {"line_no": 6, "item": "  减：营业费用", "month_amount": m_sell_exp, "ytd_amount": y_sell_exp, "is_bold": False},
        {"line_no": 7, "item": "      管理费用", "month_amount": m_admin_exp, "ytd_amount": y_admin_exp, "is_bold": False},
        {"line_no": 8, "item": "      财务费用", "month_amount": m_fin_exp, "ytd_amount": y_fin_exp, "is_bold": False},
        {"line_no": 9, "item": "三、营业利润（亏损以“-”号填列）", "month_amount": m_op_profit, "ytd_amount": y_op_profit, "is_bold": True},
        {"line_no": 10, "item": "  加：投资收益", "month_amount": m_inv_inc, "ytd_amount": y_inv_inc, "is_bold": False},
        {"line_no": 11, "item": "      营业外收入", "month_amount": m_non_op_inc, "ytd_amount": y_non_op_inc, "is_bold": False},
        {"line_no": 12, "item": "  减：营业外支出", "month_amount": m_non_op_exp, "ytd_amount": y_non_op_exp, "is_bold": False},
        {"line_no": 13, "item": "四、利润总额（亏损总额以“-”号填列）", "month_amount": m_total_profit, "ytd_amount": y_total_profit, "is_bold": True},
        {"line_no": 14, "item": "  减：所得税", "month_amount": m_tax_exp, "ytd_amount": y_tax_exp, "is_bold": False},
        {"line_no": 15, "item": "五、净利润（净亏损以“-”号填列）", "month_amount": m_net_profit, "ytd_amount": y_net_profit, "is_bold": True},
    ]
    return {"period": period, "lines": lines}


async def build_balance_sheet(db: AsyncSession, account_set_id: str, period: str) -> Dict[str, Any]:
    """3. 资产负债表（《企业会计制度》2001 标准左右对照结构，含未分配利润实时结转）"""
    coa_rows, cur_map, jan_map, ytd_dr, ytd_cr = await _load_coa_and_balances(db, account_set_id, period)
    metrics = _leaf_metrics(coa_rows, cur_map, jan_map, ytd_dr, ytd_cr)

    # 计算损益类全年累计净损益（自动并入资产负债表“未分配利润”期末数）
    ytd_pnl_net = round(
        sum(
            (v["ytd_cr"] - v["ytd_dr"])
            for k, v in metrics.items()
            if k.startswith(("51", "52", "53", "54", "55", "56", "57"))
        ),
        2,
    )

    def pair(prefixes: tuple[str, ...]):
        return (
            _sum_prefix(metrics, prefixes, "year_open"),
            _sum_prefix(metrics, prefixes, "period_close"),
        )

    # 资产部类
    cash_o, cash_c = pair(("1001", "1002", "1009"))
    ar_o, ar_c = pair(("1131",))
    oth_ar_o, oth_ar_c = pair(("1133",))
    prepay_o, prepay_c = pair(("1132",))
    inv_o, inv_c = pair(("1201", "1211", "1243"))
    def_exp_o, def_exp_c = pair(("1301",))
    cur_asset_o = round(cash_o + ar_o + oth_ar_o + prepay_o + inv_o + def_exp_o, 2)
    cur_asset_c = round(cash_c + ar_c + oth_ar_c + prepay_c + inv_c + def_exp_c, 2)

    fa_cost_o, fa_cost_c = pair(("1501",))
    fa_dep_o, fa_dep_c = pair(("1502",))
    fa_net_o = round(fa_cost_o - fa_dep_o, 2)
    fa_net_c = round(fa_cost_c - fa_dep_c, 2)
    cip_o, cip_c = pair(("1603",))
    fa_tot_o = round(fa_net_o + cip_o, 2)
    fa_tot_c = round(fa_net_c + cip_c, 2)

    intang_o, intang_c = pair(("1801", "1901"))
    tot_asset_o = round(cur_asset_o + fa_tot_o + intang_o, 2)
    tot_asset_c = round(cur_asset_c + fa_tot_c + intang_c, 2)

    # 负债及所有者权益部类
    st_loan_o, st_loan_c = pair(("2101",))
    ap_o, ap_c = pair(("2121",))
    adv_rec_o, adv_rec_c = pair(("2131",))
    payroll_o, payroll_c = pair(("2151", "2153"))
    tax_o, tax_c = pair(("2171", "2176"))
    oth_ap_o, oth_ap_c = pair(("2181",))
    cur_liab_o = round(st_loan_o + ap_o + adv_rec_o + payroll_o + tax_o + oth_ap_o, 2)
    cur_liab_c = round(st_loan_c + ap_c + adv_rec_c + payroll_c + tax_c + oth_ap_c, 2)

    lt_loan_o, lt_loan_c = pair(("2301", "2321"))
    tot_liab_o = round(cur_liab_o + lt_loan_o, 2)
    tot_liab_c = round(cur_liab_c + lt_loan_c, 2)

    cap_o, cap_c = pair(("3101",))
    cap_res_o, cap_res_c = pair(("3111",))
    sur_res_o, sur_res_c = pair(("3121",))
    ret_o, ret_base_c = pair(("3131", "3141"))
    ret_c = round(ret_base_c + ytd_pnl_net, 2)

    tot_eq_o = round(cap_o + cap_res_o + sur_res_o + ret_o, 2)
    tot_eq_c = round(cap_c + cap_res_c + sur_res_c + ret_c, 2)

    tot_liab_eq_o = round(tot_liab_o + tot_eq_o, 2)
    tot_liab_eq_c = round(tot_liab_c + tot_eq_c, 2)

    left_rows = [
        {"item": "流动资产：", "open": None, "close": None, "bold": True},
        {"item": "  货币资金", "open": cash_o, "close": cash_c, "bold": False},
        {"item": "  应收账款", "open": ar_o, "close": ar_c, "bold": False},
        {"item": "  其他应收款", "open": oth_ar_o, "close": oth_ar_c, "bold": False},
        {"item": "  预付账款", "open": prepay_o, "close": prepay_c, "bold": False},
        {"item": "  存货", "open": inv_o, "close": inv_c, "bold": False},
        {"item": "  待摊费用", "open": def_exp_o, "close": def_exp_c, "bold": False},
        {"item": "流动资产合计", "open": cur_asset_o, "close": cur_asset_c, "bold": True},
        {"item": "固定资产：", "open": None, "close": None, "bold": True},
        {"item": "  固定资产原价", "open": fa_cost_o, "close": fa_cost_c, "bold": False},
        {"item": "  减：累计折旧", "open": fa_dep_o, "close": fa_dep_c, "bold": False},
        {"item": "  固定资产净值", "open": fa_net_o, "close": fa_net_c, "bold": False},
        {"item": "  在建工程", "open": cip_o, "close": cip_c, "bold": False},
        {"item": "固定资产合计", "open": fa_tot_o, "close": fa_tot_c, "bold": True},
        {"item": "无形资产及其他资产：", "open": None, "close": None, "bold": True},
        {"item": "  无形资产及长期待摊费用", "open": intang_o, "close": intang_c, "bold": False},
        {"item": "资产总计", "open": tot_asset_o, "close": tot_asset_c, "bold": True},
    ]

    right_rows = [
        {"item": "流动负债：", "open": None, "close": None, "bold": True},
        {"item": "  短期借款", "open": st_loan_o, "close": st_loan_c, "bold": False},
        {"item": "  应付账款", "open": ap_o, "close": ap_c, "bold": False},
        {"item": "  预收账款", "open": adv_rec_o, "close": adv_rec_c, "bold": False},
        {"item": "  应付工资及福利费", "open": payroll_o, "close": payroll_c, "bold": False},
        {"item": "  应交税金及其他应交款", "open": tax_o, "close": tax_c, "bold": False},
        {"item": "  其他应付款", "open": oth_ap_o, "close": oth_ap_c, "bold": False},
        {"item": "流动负债合计", "open": cur_liab_o, "close": cur_liab_c, "bold": True},
        {"item": "长期负债：", "open": None, "close": None, "bold": True},
        {"item": "  长期借款及长期应付款", "open": lt_loan_o, "close": lt_loan_c, "bold": False},
        {"item": "负债合计", "open": tot_liab_o, "close": tot_liab_c, "bold": True},
        {"item": "所有者权益：", "open": None, "close": None, "bold": True},
        {"item": "  实收资本", "open": cap_o, "close": cap_c, "bold": False},
        {"item": "  资本公积", "open": cap_res_o, "close": cap_res_c, "bold": False},
        {"item": "  盈余公积", "open": sur_res_o, "close": sur_res_c, "bold": False},
        {"item": "  未分配利润", "open": ret_o, "close": ret_c, "bold": False},
        {"item": "负债和所有者权益总计", "open": tot_liab_eq_o, "close": tot_liab_eq_c, "bold": True},
    ]

    paired_rows = []
    for i in range(max(len(left_rows), len(right_rows))):
        l_item = left_rows[i] if i < len(left_rows) else {"item": "", "open": None, "close": None, "bold": False}
        r_item = right_rows[i] if i < len(right_rows) else {"item": "", "open": None, "close": None, "bold": False}
        paired_rows.append({
            "line_no": i + 1,
            "asset_item": l_item["item"],
            "asset_open": l_item["open"],
            "asset_close": l_item["close"],
            "asset_bold": l_item["bold"],
            "liab_item": r_item["item"],
            "liab_open": r_item["open"],
            "liab_close": r_item["close"],
            "liab_bold": r_item["bold"],
        })

    return {
        "period": period,
        "rows": paired_rows,
        "check": {
            "total_assets_open": tot_asset_o,
            "total_liab_eq_open": tot_liab_eq_o,
            "total_assets_close": tot_asset_c,
            "total_liab_eq_close": tot_liab_eq_c,
            "is_balanced": abs(tot_asset_c - tot_liab_eq_c) < 0.05,
        },
    }


async def build_subsidiary_ledger(
    db: AsyncSession, account_set_id: str, account_code: str, start_period: str, end_period: str
) -> Dict[str, Any]:
    """4. 明细表（明细分类账：含期初余额、逐笔分录流水与滚动余额）"""
    coa = (
        await db.execute(
            select(ChartOfAccount).where(
                ChartOfAccount.account_set_id == account_set_id,
                ChartOfAccount.code == account_code,
            )
        )
    ).scalar_one_or_none()
    if not coa:
        return {"account_code": account_code, "account_name": "", "rows": []}

    # 获取起始期间的期初余额（若查询上级科目则汇总下属叶子）
    all_coa = (
        await db.execute(select(ChartOfAccount).where(ChartOfAccount.account_set_id == account_set_id))
    ).scalars().all()
    target_codes = [c.code for c in all_coa if c.is_leaf and c.code.startswith(account_code)]

    op_bals = (
        await db.execute(
            select(AccountBalance).where(
                AccountBalance.account_set_id == account_set_id,
                AccountBalance.period == start_period,
                AccountBalance.account_code.in_(target_codes),
            )
        )
    ).scalars().all()

    open_net = 0.0
    for b in op_bals:
        if coa.balance_direction == "DEBIT":
            open_net = round(open_net + float(b.opening_debit) - float(b.opening_credit), 2)
        else:
            open_net = round(open_net + float(b.opening_credit) - float(b.opening_debit), 2)

    q = (
        select(Voucher, VoucherEntry)
        .join(VoucherEntry, Voucher.id == VoucherEntry.voucher_id)
        .where(
            Voucher.account_set_id == account_set_id,
            Voucher.period >= start_period,
            Voucher.period <= end_period,
            VoucherEntry.account_code.in_(target_codes),
        )
        .order_by(Voucher.voucher_date, Voucher.voucher_no, VoucherEntry.line_no)
    )
    res = (await db.execute(q)).all()

    def fmt_dir(val: float) -> str:
        if abs(val) < 0.005:
            return "平"
        return "借" if coa.balance_direction == "DEBIT" else "贷"

    running = open_net
    rows = [
        {
            "date": f"{start_period}-01",
            "voucher_no": "",
            "account_code": account_code,
            "summary": "期初余额",
            "debit": 0.0,
            "credit": 0.0,
            "direction": fmt_dir(running),
            "balance": running,
        }
    ]

    tot_dr = 0.0
    tot_cr = 0.0
    for v, e in res:
        dr = round(float(e.debit_amount), 2)
        cr = round(float(e.credit_amount), 2)
        tot_dr = round(tot_dr + dr, 2)
        tot_cr = round(tot_cr + cr, 2)
        if coa.balance_direction == "DEBIT":
            running = round(running + dr - cr, 2)
        else:
            running = round(running + cr - dr, 2)
        rows.append({
            "date": str(v.voucher_date),
            "voucher_no": f"{v.voucher_word}-{v.voucher_no}",
            "account_code": e.account_code,
            "summary": e.summary,
            "debit": dr,
            "credit": cr,
            "direction": fmt_dir(running),
            "balance": running,
        })

    rows.append({
        "date": "",
        "voucher_no": "",
        "account_code": account_code,
        "summary": "本期合计",
        "debit": tot_dr,
        "credit": tot_cr,
        "direction": fmt_dir(running),
        "balance": running,
    })

    return {
        "account_code": account_code,
        "account_name": coa.name,
        "normal_direction": coa.balance_direction,
        "rows": rows,
    }


async def export_financial_statements_excel(
    db: AsyncSession, account_set_id: str, period: str, detail_code: str = "1002"
) -> bytes:
    """导出标准四合一财务报表工作簿（资产负债表、损益表、分类表、明细表）"""
    acc_set = (
        await db.execute(select(AccountSet).where(AccountSet.id == account_set_id))
    ).scalar_one()
    bs = await build_balance_sheet(db, account_set_id, period)
    pnl = await build_income_statement(db, account_set_id, period)
    cat = await build_category_summary(db, account_set_id, period)
    sub = await build_subsidiary_ledger(db, account_set_id, detail_code, period, period)

    wb = openpyxl.Workbook()
    ws_bs = wb.active
    ws_bs.title = "资产负债表"
    ws_pnl = wb.create_sheet("损益表")
    ws_cat = wb.create_sheet("科目分类汇总表")
    ws_sub = wb.create_sheet(f"明细表_{detail_code}")

    title_font = Font(name="微软雅黑", size=14, bold=True, color="1F2937")
    hdr_font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1E3A5F")
    bold_font = Font(name="微软雅黑", size=10, bold=True)
    reg_font = Font(name="微软雅黑", size=10)
    thin = Side(style="thin", color="D1D5DB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    num_fmt = "#,##0.00"

    # Sheet 1: 资产负债表
    ws_bs.merge_cells("A1:G1")
    ws_bs["A1"] = f"资产负债表 — {acc_set.company_name}（{period}）"
    ws_bs["A1"].font = title_font
    ws_bs["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws_bs.row_dimensions[1].height = 28

    bs_headers = ["资产", "行次", "年初数", "期末数", "负债和所有者权益", "年初数", "期末数"]
    ws_bs.append(bs_headers)
    for col_idx in range(1, 8):
        c = ws_bs.cell(row=2, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.alignment = Alignment(horizontal="center")
        c.border = border

    for r in bs["rows"]:
        ws_bs.append([
            r["asset_item"], r["line_no"], r["asset_open"], r["asset_close"],
            r["liab_item"], r["liab_open"], r["liab_close"]
        ])
        curr = ws_bs.max_row
        for col_idx in range(1, 8):
            cell = ws_bs.cell(row=curr, column=col_idx)
            cell.border = border
            is_b = r["asset_bold"] if col_idx <= 4 else r["liab_bold"]
            cell.font = bold_font if is_b else reg_font
            if col_idx in (3, 4, 6, 7) and cell.value is not None:
                cell.number_format = num_fmt

    for col_letter, w in [("A", 28), ("B", 8), ("C", 18), ("D", 18), ("E", 28), ("F", 18), ("G", 18)]:
        ws_bs.column_dimensions[col_letter].width = w

    # Sheet 2: 损益表
    ws_pnl.merge_cells("A1:D1")
    ws_pnl["A1"] = f"损益表（利润表）— {acc_set.company_name}（{period}）"
    ws_pnl["A1"].font = title_font
    ws_pnl["A1"].alignment = Alignment(horizontal="center")
    ws_pnl.append(["项目", "行次", "本月数", "本年累计数"])
    for col_idx in range(1, 5):
        c = ws_pnl.cell(row=2, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = border
    for line in pnl["lines"]:
        ws_pnl.append([line["item"], line["line_no"], line["month_amount"], line["ytd_amount"]])
        curr = ws_pnl.max_row
        for col_idx in range(1, 5):
            cell = ws_pnl.cell(row=curr, column=col_idx)
            cell.border = border
            cell.font = bold_font if line["is_bold"] else reg_font
            if col_idx in (3, 4):
                cell.number_format = num_fmt
    for col_letter, w in [("A", 42), ("B", 8), ("C", 20), ("D", 20)]:
        ws_pnl.column_dimensions[col_letter].width = w

    # Sheet 3: 科目分类汇总表
    ws_cat.merge_cells("A1:K1")
    ws_cat["A1"] = f"一级科目分类汇总表 — {acc_set.company_name}（{period}）"
    ws_cat["A1"].font = title_font
    ws_cat.append([
        "科目编码", "科目名称", "类别",
        "期初借方", "期初贷方", "本期借方", "本期贷方",
        "本年累计借方", "本年累计贷方", "期末借方", "期末贷方"
    ])
    for col_idx in range(1, 12):
        c = ws_cat.cell(row=2, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = border
    for r in cat["rows"]:
        ws_cat.append([
            r["code"], r["name"], r["category"],
            r["opening_debit"], r["opening_credit"], r["period_debit"], r["period_credit"],
            r["ytd_debit"], r["ytd_credit"], r["closing_debit"], r["closing_credit"]
        ])
        curr = ws_cat.max_row
        for col_idx in range(1, 12):
            cell = ws_cat.cell(row=curr, column=col_idx)
            cell.border = border
            cell.font = reg_font
            if col_idx >= 4:
                cell.number_format = num_fmt
    t = cat["totals"]
    ws_cat.append([
        "合计", "", "",
        t["opening_debit"], t["opening_credit"], t["period_debit"], t["period_credit"],
        t["ytd_debit"], t["ytd_credit"], t["closing_debit"], t["closing_credit"]
    ])
    for col_idx in range(1, 12):
        cell = ws_cat.cell(row=ws_cat.max_row, column=col_idx)
        cell.border = border
        cell.font = bold_font
        if col_idx >= 4:
            cell.number_format = num_fmt
    for idx_c, w in enumerate([12, 24, 10, 16, 16, 16, 16, 18, 18, 16, 16], start=1):
        ws_cat.column_dimensions[openpyxl.utils.get_column_letter(idx_c)].width = w

    # Sheet 4: 明细表
    ws_sub.merge_cells("A1:H1")
    ws_sub["A1"] = f"明细分类账 [{sub['account_code']} {sub['account_name']}]（{period}）"
    ws_sub["A1"].font = title_font
    ws_sub.append(["日期", "凭证字号", "科目编码", "摘要", "借方金额", "贷方金额", "方向", "余额"])
    for col_idx in range(1, 9):
        c = ws_sub.cell(row=2, column=col_idx)
        c.font = hdr_font
        c.fill = hdr_fill
        c.border = border
    for r in sub["rows"]:
        ws_sub.append([
            r["date"], r["voucher_no"], r["account_code"], r["summary"],
            r["debit"], r["credit"], r["direction"], r["balance"]
        ])
        curr = ws_sub.max_row
        for col_idx in range(1, 9):
            cell = ws_sub.cell(row=curr, column=col_idx)
            cell.border = border
            cell.font = bold_font if r["summary"] in ("期初余额", "本期合计") else reg_font
            if col_idx in (5, 6, 8):
                cell.number_format = num_fmt
    for idx_c, w in enumerate([14, 14, 14, 38, 16, 16, 8, 18], start=1):
        ws_sub.column_dimensions[openpyxl.utils.get_column_letter(idx_c)].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
