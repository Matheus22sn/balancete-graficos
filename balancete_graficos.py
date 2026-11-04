"""Balancete (PDF) -> relatório gráfico em HTML para o cliente.

Uso: arraste o PDF do balancete sobre o .exe, ou abra o .exe e escolha o arquivo.
Gera "<nome do PDF> - Relatório.html" na mesma pasta e abre no navegador.
"""
import html
import re
import sys
import unicodedata
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

NUM_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}[DC]?$")


# ---------------------------------------------------------------- parsing

@dataclass
class Conta:
    codigo: str
    nome: str
    nivel: float          # x do início da descrição (recuo = nível hierárquico)
    anterior: float = 0.0  # saldos com sinal: D positivo, C negativo
    debito: float = 0.0
    credito: float = 0.0
    atual: float = 0.0
    pai: "Conta | None" = None
    filhos: list = field(default_factory=list)

    @property
    def chave(self):
        return sem_acento(self.nome).upper()


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


MINUSCULAS = {"de", "da", "do", "das", "dos", "e", "a", "o", "com", "por", "para", "sobre"}


def titulo(s):
    palavras = s.title().split()
    return " ".join(w.lower() if i and w.lower() in MINUSCULAS else w for i, w in enumerate(palavras))


def valor(txt):
    sinal = -1 if txt.endswith("C") else 1
    txt = txt.rstrip("DC")
    return sinal * float(txt.replace(".", "").replace(",", "."))


def ler_balancete(caminho):
    doc = pymupdf.open(caminho)
    info = {"empresa": "", "cnpj": "", "periodo": ""}
    contas = []
    for page in doc:
        words = page.get_text("words")
        linhas = {}
        for w in words:
            linhas.setdefault(round(w[1]), []).append(w)

        # posição (borda direita) das colunas numéricas, pelo cabeçalho
        colunas = None
        for y in sorted(linhas):
            ws = sorted(linhas[y], key=lambda w: w[0])
            textos = [sem_acento(w[4]).upper() for w in ws]
            if "CODIGO" in textos[0] and "ANTERIOR" in textos:
                pos = {}
                for w, t in zip(ws, textos):
                    if t == "ANTERIOR":
                        pos["anterior"] = w[2]
                    elif t == "DEBITO":
                        pos["debito"] = w[2]
                    elif t == "CREDITO":
                        pos["credito"] = w[2]
                    elif t == "ATUAL":
                        pos["atual"] = w[2]
                if len(pos) == 4:
                    colunas = pos
                break
        if colunas is None:
            continue

        for y in sorted(linhas):
            ws = sorted(linhas[y], key=lambda w: w[0])
            rotulo = sem_acento(ws[0][4]).upper()
            resto = " ".join(w[4] for w in ws[1:])
            if rotulo.startswith("EMPRESA") and not info["empresa"]:
                info["empresa"] = " ".join(w[4] for w in ws[1:] if w[0] < 400)
            elif rotulo.startswith("C.N.P.J") and not info["cnpj"]:
                info["cnpj"] = ws[1][4]
            elif rotulo.startswith("PERIODO") and not info["periodo"]:
                info["periodo"] = " ".join(w[4] for w in ws[1:] if w[0] < 400)

            # linha de conta: primeiro token é o código, e há valores numéricos
            if not ws[0][4].isdigit() or ws[0][0] > 40:
                continue
            nums = [w for w in ws[1:] if NUM_RE.match(w[4])]
            if not nums:
                continue
            desc = [w for w in ws[1:] if w not in nums]
            if not desc:
                continue
            c = Conta(ws[0][4], " ".join(w[4] for w in desc), round(desc[0][0]))
            for n in nums:
                col = min(colunas, key=lambda k: abs(colunas[k] - n[2]))
                setattr(c, col, valor(n[4]))
            # débito/crédito são movimentos: sempre positivos
            c.debito, c.credito = abs(c.debito), abs(c.credito)
            contas.append(c)
            del resto

    # hierarquia pelo recuo
    pilha = []
    for c in contas:
        while pilha and pilha[-1].nivel >= c.nivel:
            pilha.pop()
        if pilha:
            c.pai = pilha[-1]
            pilha[-1].filhos.append(c)
        pilha.append(c)
    return info, contas


# ---------------------------------------------------------------- análise

def subarvore(raiz):
    for f in raiz.filhos:
        yield f
        yield from subarvore(f)


def classificar(folha):
    """Retorna (classe, sinal) de uma conta de resultado analítica."""
    no = folha
    menos_entre = False
    while no is not None:
        k = no.chave
        if no is not folha and "(-)" in k:
            menos_entre = True
        classe = None
        if "DEDU" in k:
            classe = "deducao"
        elif "CUSTO" in k:
            classe = "custo"
        elif "DESPESA" in k:
            classe = "despesa"
        elif "RECEITA" in k:
            classe = "receita" if "BRUTA" in k or "VENDA" in k else "outras"
        if classe:
            # "(-)" na própria conta inverte (ex.: devolução de compra reduz o custo),
            # a menos que a conta-mãe já seja redutora (ex.: "(-) Deduções")
            inverte = "(-)" in folha.chave and no is not folha and not menos_entre and "(-)" not in k
            return classe, (-1 if inverte else 1)
        no = no.pai
    return ("despesa" if folha.debito >= folha.credito else "outras"), 1


def montante(c, classe):
    """Movimento da conta no período. Contas zeradas pela apuração têm D = C."""
    if abs(c.atual) < 0.005 and abs(c.debito - c.credito) < 0.005:
        return c.debito
    devedora = classe in ("deducao", "custo", "despesa")
    return c.atual if devedora else -c.atual


def analisar(contas):
    raiz = next((c for c in contas if "CONTAS DE RESULTADO" in c.chave), None)
    if raiz is None:
        raise ValueError("Não encontrei o grupo 'CONTAS DE RESULTADOS' no balancete.")

    itens = []
    for c in subarvore(raiz):
        if c.filhos:
            continue
        classe, sinal = classificar(c)
        itens.append({"conta": c, "nome": titulo(c.nome), "classe": classe,
                      "valor": sinal * montante(c, classe)})

    tot = {k: sum(i["valor"] for i in itens if i["classe"] == k)
           for k in ("receita", "deducao", "custo", "despesa", "outras")}
    tot["lucro"] = tot["receita"] - tot["deducao"] - tot["custo"] - tot["despesa"] + tot["outras"]

    lucro_balancete = None
    for c in contas:
        k = c.chave
        if ("LUCRO DO EXERC" in k or "PREJUIZO DO EXERC" in k or "RESULTADO DO EXERC" in k) \
                and not c.filhos and c.pai and "APURA" not in c.pai.chave:
            lucro_balancete = max(c.debito, c.credito) or abs(c.atual)
            if "PREJUIZO" in k:
                lucro_balancete = -lucro_balancete
            break

    def saldo(*nomes):
        for c in contas:
            if any(c.chave == n for n in nomes):
                return abs(c.atual)
        return None

    patrimonio = [
        ("Caixa e bancos", saldo("DISPONIVEL")),
        ("A receber de clientes", saldo("CLIENTES")),
        ("Estoque", saldo("ESTOQUE")),
        ("A pagar a fornecedores", saldo("FORNECEDORES")),
    ]

    fornecedores = []
    grupo = next((c for c in contas if c.chave == "FORNECEDORES" and c.filhos), None)
    if grupo:
        for c in subarvore(grupo):
            if not c.filhos and c.credito > 0:
                fornecedores.append((titulo(c.nome), c.credito, abs(c.atual)))
        fornecedores.sort(key=lambda f: -f[1])

    return {"itens": itens, "tot": tot, "lucro_balancete": lucro_balancete,
            "patrimonio": [p for p in patrimonio if p[1] is not None],
            "fornecedores": fornecedores}


# ---------------------------------------------------------------- formatação

def brl(v, casas=2):
    s = f"{abs(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-R$ " if v < 0 else "R$ ") + s


def compacto(v):
    a = abs(v)
    if a >= 1_000_000:
        s = f"{a / 1_000_000:.1f}".replace(".", ",") + " mi"
    elif a >= 1_000:
        s = f"{a / 1_000:.0f} mil" if a >= 10_000 else f"{a / 1_000:.1f}".replace(".", ",") + " mil"
    else:
        s = f"{a:.0f}"
    return ("-" if v < 0 else "") + "R$ " + s


def pct(v):
    return f"{v * 100:.1f}%".replace(".", ",")


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- gráficos (SVG)

def barra_h(x, y, w, h, r=4):
    """Barra horizontal: quadrada na base (esquerda), arredondada na ponta."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f}H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}"
            f"V{y + h - r:.1f}Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f}H{x:.1f}Z")


def grafico_cascata(tot):
    passos = [("Receita bruta", tot["receita"], "total"),
              ("Impostos", -tot["deducao"], "menos"),
              ("Custo das mercadorias", -tot["custo"], "menos"),
              ("Despesas", -tot["despesa"], "menos")]
    if tot["outras"]:
        passos.append(("Outras receitas", tot["outras"], "mais"))
    passos.append(("Lucro" if tot["lucro"] >= 0 else "Prejuízo", tot["lucro"], "total"))

    barras, acum = [], 0.0
    for nome, v, tipo in passos:
        if tipo == "total":
            ini, fim = 0.0, v
            acum = v
        else:
            ini, fim = acum, acum + v
            acum = fim
        barras.append((nome, v, tipo, min(ini, fim), max(ini, fim), acum))

    W, H, top, bot, esq = 760, 340, 34, 56, 64
    lo = min(0.0, min(b[3] for b in barras))
    hi = max(b[4] for b in barras) or 1
    esc_y = lambda v: top + (hi - v) / (hi - lo) * (H - top - bot)
    banda = (W - esq - 10) / len(barras)
    bw = min(56, banda * 0.6)

    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Cascata do resultado">']
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = esc_y(v)
        out.append(f'<line x1="{esq}" x2="{W - 10}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{esq - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{compacto(v)}</text>')
    y0 = esc_y(0)
    out.append(f'<line x1="{esq}" x2="{W - 10}" y1="{y0:.1f}" y2="{y0:.1f}" class="axis"/>')

    nivel_ant = None
    for i, (nome, v, tipo, a, b, nivel) in enumerate(barras):
        cx = esq + banda * i + banda / 2
        x = cx - bw / 2
        ya, yb = esc_y(b), esc_y(a)
        h = max(yb - ya, 1.5)
        cls = {"total": "s-total", "menos": "s-menos", "mais": "s-mais"}[tipo]
        if tipo == "total" and v < 0:
            cls = "s-menos"
        r = min(4, h / 2)
        # retângulo com cantos superiores arredondados (barras de total crescem da base)
        path = (f"M{x:.1f},{yb:.1f}V{ya + r:.1f}Q{x:.1f},{ya:.1f} {x + r:.1f},{ya:.1f}"
                f"H{x + bw - r:.1f}Q{x + bw:.1f},{ya:.1f} {x + bw:.1f},{ya + r:.1f}V{yb:.1f}Z")
        tip = f"{nome}: {brl(v)}"
        out.append(f'<path d="{path}" class="{cls}" data-tip="{esc(tip)}"/>')
        if nivel_ant is not None:
            # liga o nível acumulado da barra anterior ao início desta
            yl = esc_y(nivel_ant)
            out.append(f'<line x1="{x - (banda - bw):.1f}" x2="{x:.1f}" y1="{yl:.1f}" y2="{yl:.1f}" class="conn"/>')
        nivel_ant = nivel
        sinal = "−" if v < 0 and tipo != "total" else ("+" if tipo == "mais" else "")
        out.append(f'<text x="{cx:.1f}" y="{ya - 8:.1f}" class="val" text-anchor="middle">{sinal}{compacto(abs(v)) if tipo != "total" else compacto(v)}</text>')
        palavras = nome.split()
        linhas = [" ".join(palavras[:2]), " ".join(palavras[2:])] if len(palavras) > 2 else [nome]
        for j, l in enumerate(linhas):
            out.append(f'<text x="{cx:.1f}" y="{H - bot + 20 + j * 15}" class="lbl" text-anchor="middle">{esc(l)}</text>')
    out.append("</svg>")
    return "".join(out)


def grafico_ranking(itens, base, rotulo_base):
    """Barras horizontais ordenadas. itens: [(nome, valor)]."""
    if not itens:
        return '<p class="muted">Sem dados.</p>'
    itens = sorted(itens, key=lambda t: -t[1])
    W, linha, bh, esq, dir_ = 760, 36, 22, 250, 170
    H = linha * len(itens) + 8
    mx = max(v for _, v in itens) or 1
    larg = W - esq - dir_
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Ranking">']
    out.append(f'<line x1="{esq}" x2="{esq}" y1="0" y2="{H}" class="axis"/>')
    for i, (nome, v) in enumerate(itens):
        y = 4 + i * linha + (linha - bh) / 2
        w = max(v / mx * larg, 2)
        nome_curto = nome if len(nome) <= 34 else nome[:32] + "…"
        tip = f"{nome}: {brl(v)}" + (f" ({pct(v / base)} {rotulo_base})" if base else "")
        out.append(f'<text x="{esq - 10}" y="{y + bh / 2 + 4:.1f}" class="lbl" text-anchor="end">{esc(nome_curto)}</text>')
        out.append(f'<rect x="0" y="{y - 6:.1f}" width="{W}" height="{linha}" class="hit" data-tip="{esc(tip)}"/>')
        out.append(f'<path d="{barra_h(esq, y, w, bh)}" class="{"s-menos" if i == 0 else "s-menos-2"}" data-tip="{esc(tip)}"/>')
        txt = brl(v) + (f"  ·  {pct(v / base)}" if base else "")
        out.append(f'<text x="{esq + w + 8:.1f}" y="{y + bh / 2 + 4:.1f}" class="val">{txt}</text>')
    out.append("</svg>")
    return "".join(out)


def barra_100(tot):
    """De cada R$ 100 que entraram: custo, impostos, despesas, lucro."""
    base = tot["receita"] + tot["outras"]
    if base <= 0 or tot["lucro"] < 0:
        return ""
    partes = [("Custo das mercadorias", tot["custo"], "c1"), ("Impostos", tot["deducao"], "c2"),
              ("Despesas", tot["despesa"], "c3"), ("Lucro", tot["lucro"], "c4")]
    W, H, gap = 760, 44, 2
    x, out = 0.0, [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Composição de cada R$ 100">']
    vis = [p for p in partes if p[1] > 0]
    for i, (nome, v, cls) in enumerate(vis):
        w = v / base * W - (gap if i < len(vis) - 1 else 0)
        tip = f"{nome}: {brl(v)} — R$ {v / base * 100:.2f} de cada R$ 100".replace(".", ",")
        out.append(f'<rect x="{x:.1f}" y="0" width="{max(w, 1):.1f}" height="{H}" rx="4" class="{cls}" data-tip="{esc(tip)}"/>')
        rot = f"R$ {v / base * 100:.0f}"
        if w > len(rot) * 8 + 16:
            out.append(f'<text x="{x + w / 2:.1f}" y="{H / 2 + 5}" class="inbar {cls}-t" text-anchor="middle">{rot}</text>')
        x += w + gap
    out.append("</svg>")
    legenda = "".join(f'<span class="key"><i class="{cls}"></i>{esc(n)} <b>{brl(v / base * 100)}</b></span>'
                      for n, v, cls in vis)
    return "".join(out) + f'<div class="legend">{legenda}</div>'


# ---------------------------------------------------------------- relatório

def gerar_html(info, dados, origem):
    tot, itens = dados["tot"], dados["itens"]
    receita = tot["receita"]
    margem = tot["lucro"] / receita if receita else 0

    gastos = [(i["nome"].replace("(-) ", ""), i["valor"]) for i in itens if i["classe"] in ("despesa", "deducao") and i["valor"] > 0.004]
    maior = max(gastos, key=lambda t: t[1]) if gastos else ("—", 0)
    gastos_todos = list(gastos)
    if tot["custo"] > 0:
        gastos_todos.append(("Custo das mercadorias vendidas", tot["custo"]))
    despesas_op = [(i["nome"], i["valor"]) for i in itens if i["classe"] == "despesa" and i["valor"] > 0.004]

    lb = dados["lucro_balancete"]
    if lb is None:
        conf = '<p class="note">Não encontrei a conta "Lucro do Exercício" para conferência.</p>'
    elif abs(lb - tot["lucro"]) < 0.05:
        conf = f'<p class="note ok">✔ Conferido: o resultado calculado bate com o "Lucro do Exercício" do balancete ({brl(lb)}).</p>'
    else:
        conf = (f'<p class="note warn">⚠ Atenção: resultado calculado ({brl(tot["lucro"])}) difere do balancete '
                f'({brl(lb)}). Revise a classificação das contas.</p>')

    def linha_dre(nome, v, cls=""):
        p = pct(v / receita) if receita else ""
        return f'<tr class="{cls}"><td>{esc(nome)}</td><td class="n">{brl(v)}</td><td class="n">{p}</td></tr>'

    dre = [linha_dre("Receita bruta de vendas", tot["receita"], "strong")]
    for i in itens:
        if i["classe"] == "receita" and len([x for x in itens if x["classe"] == "receita"]) > 1:
            dre.append(linha_dre("  " + i["nome"], i["valor"], "sub"))
    dre.append(linha_dre("(−) Impostos sobre vendas", -tot["deducao"]))
    dre += [linha_dre("  " + i["nome"].replace("(-) ", ""), -i["valor"], "sub") for i in itens if i["classe"] == "deducao"]
    dre.append(linha_dre("Receita líquida", tot["receita"] - tot["deducao"], "strong"))
    dre.append(linha_dre("(−) Custo das mercadorias vendidas", -tot["custo"]))
    dre += [linha_dre("  " + i["nome"], -i["valor"], "sub") for i in itens if i["classe"] == "custo"]
    bruto = tot["receita"] - tot["deducao"] - tot["custo"]
    dre.append(linha_dre("Lucro bruto", bruto, "strong"))
    dre.append(linha_dre("(−) Despesas", -tot["despesa"]))
    dre += [linha_dre("  " + i["nome"], -i["valor"], "sub") for i in itens if i["classe"] == "despesa"]
    if tot["outras"]:
        dre.append(linha_dre("(+) Outras receitas", tot["outras"]))
        dre += [linha_dre("  " + i["nome"], i["valor"], "sub") for i in itens if i["classe"] == "outras"]
    dre.append(linha_dre("Lucro líquido" if tot["lucro"] >= 0 else "Prejuízo", tot["lucro"], "total"))

    patrimonio = "".join(
        f'<div class="tile small"><div class="t-lbl">{esc(n)}</div><div class="t-val">{brl(v)}</div></div>'
        for n, v in dados["patrimonio"])

    forn = dados["fornecedores"][:10]
    forn_html = ""
    if forn:
        forn_html = f"""
<section>
  <h2>Maiores fornecedores</h2>
  <p class="sub">Compras lançadas no período (créditos na conta de cada fornecedor) e o saldo que ainda está em aberto.</p>
  {grafico_ranking([(n, c) for n, c, _ in forn], sum(f[1] for f in dados["fornecedores"]), "das compras")}
  <details><summary>Ver tabela</summary><table><thead><tr><th>Fornecedor</th><th class="n">Compras</th><th class="n">Em aberto</th></tr></thead><tbody>
  {"".join(f'<tr><td>{esc(n)}</td><td class="n">{brl(c)}</td><td class="n">{brl(s)}</td></tr>' for n, c, s in dados["fornecedores"])}
  </tbody></table></details>
</section>"""

    lucro_cls = "pos" if tot["lucro"] >= 0 else "neg"
    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Relatório do balancete</title>
<style>
:root {{
  color-scheme: light;
  --bg:#f4f3f0; --surface:#fcfcfb; --line:#e4e3de; --line-2:#cfcdc6;
  --ink:#0b0b0b; --ink-2:#52514e; --ink-3:#7a7974;
  --total:#2a78d6; --menos:#eb6834; --menos-2:#f2a482; --mais:#1baf7a;
  --c1:#2a78d6; --c2:#eb6834; --c3:#1baf7a; --c4:#eda100;
  --pos:#1d7a3a; --neg:#c43c3b;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  color-scheme: dark;
  --bg:#111110; --surface:#1a1a19; --line:#2c2c2a; --line-2:#3c3c39;
  --ink:#f0efec; --ink-2:#c3c2b7; --ink-3:#8f8e86;
  --total:#3987e5; --menos:#d95926; --menos-2:#8f4426; --mais:#199e70;
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500;
  --pos:#4cc07a; --neg:#e66767;
}} }}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --bg:#111110; --surface:#1a1a19; --line:#2c2c2a; --line-2:#3c3c39;
  --ink:#f0efec; --ink-2:#c3c2b7; --ink-3:#8f8e86;
  --total:#3987e5; --menos:#d95926; --menos-2:#8f4426; --mais:#199e70;
  --c1:#3987e5; --c2:#d95926; --c3:#199e70; --c4:#c98500;
  --pos:#4cc07a; --neg:#e66767;
}}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; }}
main {{ max-width:880px; margin:0 auto; padding:28px 16px 60px }}
header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start; flex-wrap:wrap }}
h1 {{ font-size:24px; margin:0 0 4px }} h2 {{ font-size:18px; margin:0 0 2px }}
.meta, .sub, .muted {{ color:var(--ink-2); margin:0 }} .sub {{ margin-bottom:14px; font-size:14px }}
.actions button {{ font:inherit; font-size:13px; padding:7px 12px; border-radius:8px; border:1px solid var(--line-2);
  background:var(--surface); color:var(--ink); cursor:pointer; margin-left:6px }}
section {{ background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:20px; margin-top:18px }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-top:18px }}
.tile {{ background:var(--surface); border:1px solid var(--line); border-radius:14px; padding:16px }}
.tile.hero .t-val {{ font-size:30px }}
.t-lbl {{ color:var(--ink-2); font-size:13px }} .t-val {{ font-size:22px; font-weight:600; margin-top:2px; white-space:nowrap }}
.t-sub {{ color:var(--ink-3); font-size:13px }}
.tile.small .t-val {{ font-size:18px }}
.pos {{ color:var(--pos) }} .neg {{ color:var(--neg) }}
.chart {{ width:100%; height:auto; display:block; overflow:visible }}
.grid {{ stroke:var(--line); stroke-width:1 }} .axis {{ stroke:var(--line-2); stroke-width:1 }}
.conn {{ stroke:var(--ink-3); stroke-width:1 }}
.tick {{ fill:var(--ink-3); font-size:12px }} .lbl {{ fill:var(--ink-2); font-size:13px }}
.val {{ fill:var(--ink); font-size:13px; font-weight:600 }}
.s-total {{ fill:var(--total) }} .s-menos {{ fill:var(--menos) }} .s-menos-2 {{ fill:var(--menos-2) }} .s-mais {{ fill:var(--mais) }}
.c1 {{ fill:var(--c1); background:var(--c1) }} .c2 {{ fill:var(--c2); background:var(--c2) }}
.c3 {{ fill:var(--c3); background:var(--c3) }} .c4 {{ fill:var(--c4); background:var(--c4) }}
.inbar {{ font-size:14px; font-weight:600; fill:#fff }} .c4-t, .c3-t {{ fill:#0b0b0b }}
.hit {{ fill:transparent }}
path[data-tip]:hover, rect[data-tip]:not(.hit):hover {{ opacity:.85 }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 18px; margin-top:12px; font-size:13px; color:var(--ink-2) }}
.key i {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:0 }}
.key b {{ color:var(--ink) }}
table {{ width:100%; border-collapse:collapse; font-size:14px; margin-top:8px }}
th, td {{ padding:7px 8px; border-bottom:1px solid var(--line); text-align:left }}
th {{ color:var(--ink-2); font-weight:600 }} .n {{ text-align:right; white-space:nowrap }}
tr.sub td {{ color:var(--ink-2); padding-left:22px; font-size:13px }}
tr.strong td {{ font-weight:600 }} tr.total td {{ font-weight:700; border-top:2px solid var(--line-2); font-size:15px }}
details {{ margin-top:10px }} summary {{ cursor:pointer; color:var(--ink-2); font-size:13px }}
.note {{ font-size:13px; color:var(--ink-2); margin:12px 0 0 }} .note.ok {{ color:var(--pos) }} .note.warn {{ color:var(--neg) }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--surface); font-size:13px;
  padding:6px 9px; border-radius:6px; opacity:0; transition:opacity .1s; z-index:10; max-width:320px }}
footer {{ color:var(--ink-3); font-size:12px; margin-top:24px; text-align:center }}
@media print {{
  body {{ background:#fff }} .actions, #tip {{ display:none }}
  section, .tile {{ break-inside:avoid; border-color:#ddd }} details {{ display:none }}
  main {{ padding-top:0 }}
}}
</style></head>
<body><main>
<header>
  <div>
    <h1>{esc(info["empresa"] or "Relatório do balancete")}</h1>
    <p class="meta">CNPJ {esc(info["cnpj"])} · Período {esc(info["periodo"])}</p>
  </div>
  <div class="actions"><button onclick="tema()">Claro/escuro</button><button onclick="print()">Imprimir / PDF</button></div>
</header>

<div class="tiles">
  <div class="tile hero"><div class="t-lbl">{"Lucro líquido" if tot["lucro"] >= 0 else "Prejuízo"}</div>
    <div class="t-val {lucro_cls}">{brl(tot["lucro"])}</div><div class="t-sub">margem de {pct(margem)} sobre as vendas</div></div>
  <div class="tile"><div class="t-lbl">Vendas (receita bruta)</div><div class="t-val">{brl(receita)}</div></div>
  <div class="tile"><div class="t-lbl">Custo das mercadorias</div><div class="t-val">{brl(tot["custo"])}</div>
    <div class="t-sub">{pct(tot["custo"] / receita) if receita else ""} das vendas</div></div>
  <div class="tile"><div class="t-lbl">Maior despesa</div><div class="t-val">{brl(maior[1])}</div>
    <div class="t-sub">{esc(maior[0])}</div></div>
</div>

<section>
  <h2>Da venda ao lucro</h2>
  <p class="sub">Quanto entrou com as vendas e o que foi saindo até chegar ao resultado do período.</p>
  {grafico_cascata(tot)}
  <div class="legend"><span class="key"><i style="background:var(--total)"></i>Totais</span>
    <span class="key"><i style="background:var(--menos)"></i>Saídas</span>
    {'<span class="key"><i style="background:var(--mais)"></i>Entradas extras</span>' if tot["outras"] else ''}</div>
</section>

{f'''<section>
  <h2>De cada R$ 100 que entraram</h2>
  <p class="sub">Para onde foi cada R$ 100 recebido (vendas + outras receitas).</p>
  {barra_100(tot)}
</section>''' if barra_100(tot) else ''}

<section>
  <h2>Onde o dinheiro foi gasto</h2>
  <p class="sub">Todos os gastos do período, do maior para o menor, com o percentual sobre as vendas.</p>
  {grafico_ranking(gastos_todos, receita, "das vendas")}
</section>

<section>
  <h2>Despesas do dia a dia</h2>
  <p class="sub">Só as despesas operacionais (sem o custo das mercadorias e os impostos), para enxergar as menores.</p>
  {grafico_ranking(despesas_op, receita, "das vendas")}
</section>

{forn_html}

{f'<section><h2>Situação no fim do período</h2><p class="sub">Saldos em aberto na data final do balancete.</p><div class="tiles" style="margin-top:0">{patrimonio}</div></section>' if patrimonio else ''}

<section>
  <h2>Demonstração do resultado</h2>
  <p class="sub">Os números de todos os gráficos acima, em tabela.</p>
  <table><thead><tr><th>Conta</th><th class="n">Valor</th><th class="n">% vendas</th></tr></thead>
  <tbody>{"".join(dre)}</tbody></table>
  {conf}
</section>

<footer>Gerado a partir de {esc(origem)}</footer>
</main>
<div id="tip"></div>
<script>
const tip = document.getElementById('tip');
document.addEventListener('mousemove', e => {{
  const t = e.target.closest('[data-tip]');
  if (!t) {{ tip.style.opacity = 0; return; }}
  tip.textContent = t.dataset.tip;
  tip.style.opacity = 1;
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px';
}});
function tema() {{
  const r = document.documentElement;
  const escuro = r.dataset.theme ? r.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  r.dataset.theme = escuro ? 'light' : 'dark';
}}
</script>
</body></html>"""


# ---------------------------------------------------------------- entrada

def escolher_pdf():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    p = filedialog.askopenfilename(title="Escolha o PDF do balancete", filetypes=[("PDF", "*.pdf")])
    root.destroy()
    return p


def avisar(msg, erro=False):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if erro else messagebox.showinfo)("Balancete → Gráficos", msg)
        root.destroy()
    except Exception:
        print(msg)


def processar(pdf, abrir=True):
    pdf = Path(pdf)
    info, contas = ler_balancete(pdf)
    if not contas:
        raise ValueError("Não reconheci nenhuma conta neste PDF. Ele está no formato de balancete esperado?")
    dados = analisar(contas)
    saida = pdf.with_name(f"{pdf.stem} - Relatório.html")
    saida.write_text(gerar_html(info, dados, pdf.name), encoding="utf-8")
    if abrir:
        webbrowser.open(saida.resolve().as_uri())
    return saida, dados


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    abrir = "--sem-abrir" not in sys.argv
    pdfs = args or [escolher_pdf()]
    pdfs = [p for p in pdfs if p]
    if not pdfs:
        return
    for p in pdfs:
        try:
            saida, _ = processar(p, abrir)
            print(f"Gerado: {saida}")
        except Exception as e:
            avisar(f"Não consegui processar:\n{p}\n\n{e}", erro=True)


if __name__ == "__main__":
    main()
