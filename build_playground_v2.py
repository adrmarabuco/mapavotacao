"""Gera mapa_votacao_v2.html: v1 (Range Map / Brazil Map por candidato) + brancos, nulos e abstenções."""
import json
import os

import geopandas as gpd
import pandas as pd
import shapely

from build_playground import (ALIASES, BASE, COS_LAT, SCALE, SHP_MUN, SHP_UF, TOL_MUN, TOL_UF,
                              load_votes, norm, proj, to_path)

MUNZONA = os.path.join(BASE, 'detalhe_votacao_munzona_2026', 'detalhe_votacao_munzona_2026_BR.csv')
TEMPLATE = os.path.join(BASE, 'playground_template_v2.html')
OUT = os.path.join(BASE, 'mapa_votacao_v2.html')

# ordem do vetor `a` no payload
A_COLS = ['QT_APTOS', 'QT_COMPARECIMENTO', 'QT_ABSTENCOES', 'QT_VOTOS_BRANCOS', 'QT_TOTAL_VOTOS_NULOS']
MUN_IDX = ['SG_UF', 'CD_MUNICIPIO', 'NM_MUNICIPIO']


def load_turnout():
    df = pd.read_csv(MUNZONA, encoding='latin1', sep=';')
    df = df[(df.NR_TURNO == 1) & (df.CD_CARGO == 1)]
    mun = df.groupby(MUN_IDX, as_index=False)[A_COLS].sum()
    uf = df.groupby('SG_UF')[A_COLS].sum()
    return mun, uf


def main():
    votes = load_votes()
    votes = votes[votes.CD_CARGO == 1]
    cands = (votes.groupby(['NR_VOTAVEL', 'NM_VOTAVEL']).QT_VOTOS.sum()
             .reset_index().sort_values('QT_VOTOS', ascending=False))
    nums = [int(n) for n in cands.NR_VOTAVEL]
    candidates = [{'nr': int(r.NR_VOTAVEL), 'nm': r.NM_VOTAVEL, 'votes': int(r.QT_VOTOS)}
                  for r in cands.itertuples()]

    vot_mun = votes.pivot_table(index=MUN_IDX, columns='NR_VOTAVEL', values='QT_VOTOS',
                                aggfunc='sum', fill_value=0).reset_index()
    vot_uf = votes.pivot_table(index='SG_UF', columns='NR_VOTAVEL', values='QT_VOTOS',
                               aggfunc='sum', fill_value=0)

    turn_mun, turn_uf = load_turnout()

    # votos x comparecimento: mesma fonte (TSE), junção pelo código do município
    mun = vot_mun.merge(turn_mun, on=MUN_IDX, how='outer')
    sem_turn = mun[A_COLS[0]].isna().sum()
    sem_vot = mun[nums[0]].isna().sum()
    print(f'municípios TSE sem comparecimento: {sem_turn} | sem votos por candidato: {sem_vot}')
    mun = mun.fillna(0)

    # checagens contra os notebooks
    bn = turn_uf.QT_VOTOS_BRANCOS + turn_uf.QT_TOTAL_VOTOS_NULOS
    print('brancos+nulos AC =', int(bn['AC']), '| MG =', int(bn['MG']), '(nullMap: 19264 | 661039)')
    print('totais BR:', {c: int(turn_uf[c].sum()) for c in A_COLS})
    diverg = (turn_mun.QT_COMPARECIMENTO != turn_mun.QT_APTOS - turn_mun.QT_ABSTENCOES).sum()
    print('municípios com comparecimento != aptos - abstenções:', diverg)

    # --- geometria
    mapa = gpd.read_file(SHP_MUN, encoding='utf-8')
    mapa_uf = gpd.read_file(SHP_UF, encoding='utf-8')
    minx, miny, maxx, maxy = mapa_uf.total_bounds
    width = round((maxx - minx) * COS_LAT * SCALE)
    height = round((maxy - miny) * SCALE)

    mun['key'] = mun.SG_UF + '|' + [ALIASES.get((u, norm(n)), norm(n)) for u, n in zip(mun.SG_UF, mun.NM_MUNICIPIO)]
    mapa['key'] = mapa.SIGLA + '|' + mapa.NM_MUN.map(norm)
    by_key = mun.set_index('key')

    unmatched_map = mapa[~mapa.key.isin(by_key.index)]
    tse_br = mun[mun.SG_UF != 'ZZ']
    unmatched_tse = tse_br[~tse_br.key.isin(mapa.key)]
    print(f'municípios IBGE sem votos: {len(unmatched_map)}', list(unmatched_map.key))
    print(f'municípios TSE sem geometria: {len(unmatched_tse)}', list(unmatched_tse.key))

    simp_mun = shapely.simplify(mapa.geometry.values, TOL_MUN, preserve_topology=True)
    muns = []
    for (_, row), g in zip(mapa.iterrows(), simp_mun):
        r = by_key.loc[row.key] if row.key in by_key.index else None
        c = g.representative_point()
        muns.append({
            'nm': row.NM_MUN, 'uf': row.SIGLA,
            'd': to_path(g, minx, maxy),
            'c': proj(c.x, c.y, minx, maxy),
            'v': [int(r[n]) for n in nums] if r is not None else None,
            'a': [int(r[k]) for k in A_COLS] if r is not None else None,
        })

    simp_uf = shapely.simplify(mapa_uf.geometry.values, TOL_UF, preserve_topology=True)
    ufs = []
    for (_, row), g in zip(mapa_uf.iterrows(), simp_uf):
        b = g.bounds
        c = g.representative_point()
        ufs.append({
            'sg': row.SIGLA, 'nm': row.NM_UF,
            'd': to_path(g, minx, maxy),
            'c': proj(c.x, c.y, minx, maxy),
            'bb': proj(b[0], b[3], minx, maxy) + proj(b[2], b[1], minx, maxy),
            'v': [int(vot_uf.loc[row.SIGLA, n]) for n in nums],
            'a': [int(turn_uf.loc[row.SIGLA, k]) for k in A_COLS],
        })

    exterior = [int(vot_uf.loc['ZZ', n]) for n in nums] if 'ZZ' in vot_uf.index else None
    exterior_a = [int(turn_uf.loc['ZZ', k]) for k in A_COLS] if 'ZZ' in turn_uf.index else None

    data = {'w': width, 'h': height, 'candidates': candidates, 'ufs': ufs, 'muns': muns,
            'exterior': exterior, 'exteriorA': exterior_a}
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read().replace('/*__DATA__*/null', payload)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'{OUT}: {os.path.getsize(OUT) / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
