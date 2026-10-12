"""Gera mapa_votacao.html (playground Range Map / Brazil Map) a partir dos dados do notebook rangeMap.ipynb."""
import json
import math
import os
import re
import unicodedata

import geopandas as gpd
import pandas as pd
import shapely

BASE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(BASE, 'votacao_secao_2026_BR', 'votacao_secao_2026_BR.csv')
SHP_MUN = os.path.join(BASE, 'BR_Municipios_2021', 'BR_Municipios_2021.shp')
SHP_UF = os.path.join(BASE, 'BR_UF_2021', 'BR_UF_2021.shp')
CACHE = os.path.join(BASE, '.cache_votos_t1.pkl')
TEMPLATE = os.path.join(BASE, 'playground_template.html')
OUT = os.path.join(BASE, 'mapa_votacao.html')

TOL_MUN = 0.005  # graus
TOL_UF = 0.02
SCALE = 100      # unidades SVG por grau
COS_LAT = math.cos(math.radians(15))

# Grafias que diferem entre TSE e IBGE: (UF, nome TSE normalizado) -> nome IBGE normalizado
ALIASES = {
    ('GO', 'BOM JESUS'): 'BOM JESUS DE GOIAS',
    ('MG', 'DONA EUSEBIA'): 'DONA EUZEBIA',
    ('MG', 'SAO THOME DAS LETRAS'): 'SAO TOME DAS LETRAS',
    ('PA', 'ELDORADO DOS CARAJAS'): 'ELDORADO DO CARAJAS',
    ('PR', 'MUNHOZ DE MELLO'): 'MUNHOZ DE MELO',
    ('RN', 'ASSU'): 'ACU',
    ('RN', 'AREZ'): 'ARES',
    ('RN', 'BOA SAUDE'): 'JANUARIO CICCO',
    ('RO', 'ESPIGAO DO OESTE'): 'ESPIGAO D OESTE',
    ('RO', 'ALVORADA DO OESTE'): 'ALVORADA D OESTE',
    ('SE', 'AMPARO DE SAO FRANCISCO'): 'AMPARO DO SAO FRANCISCO',
    ('SE', 'GRACCHO CARDOSO'): 'GRACHO CARDOSO',
    ('SP', 'SAO LUIS DO PARAITINGA'): 'SAO LUIZ DO PARAITINGA',
}
# Sem correspondência esperada: RS|LAGOA MIRIM e RS|LAGOA DOS PATOS (corpos d'água no IBGE) e
# MT|BOA ESPERANCA DO NORTE (município instalado depois da malha de 2021).


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    s = re.sub(r"['`\-]", ' ', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def load_votes():
    if os.path.exists(CACHE):
        return pd.read_pickle(CACHE)
    cols = ['NR_TURNO', 'CD_CARGO', 'SG_UF', 'CD_MUNICIPIO', 'NM_MUNICIPIO',
            'NR_VOTAVEL', 'NM_VOTAVEL', 'QT_VOTOS']
    parts = []
    for chunk in pd.read_csv(CSV, encoding='latin1', sep=';', usecols=cols, chunksize=1_000_000):
        chunk = chunk[chunk.NR_TURNO == 1]
        parts.append(chunk.groupby(['CD_CARGO', 'SG_UF', 'CD_MUNICIPIO', 'NM_MUNICIPIO',
                                    'NR_VOTAVEL', 'NM_VOTAVEL'], as_index=False).QT_VOTOS.sum())
        print('.', end='', flush=True)
    print()
    df = pd.concat(parts).groupby(['CD_CARGO', 'SG_UF', 'CD_MUNICIPIO', 'NM_MUNICIPIO',
                                   'NR_VOTAVEL', 'NM_VOTAVEL'], as_index=False).QT_VOTOS.sum()
    df.to_pickle(CACHE)
    return df


def to_path(geom, minx, maxy):
    """Geometria (lon/lat) -> string de path SVG em coordenadas inteiras projetadas."""
    polys = geom.geoms if geom.geom_type == 'MultiPolygon' else [geom]
    out = []
    for p in polys:
        for ring in [p.exterior, *p.interiors]:
            pts, last = [], None
            for x, y in ring.coords:
                q = (round((x - minx) * COS_LAT * SCALE), round((maxy - y) * SCALE))
                if q != last:
                    pts.append(q)
                    last = q
            if len(pts) < 4:
                continue
            out.append('M' + 'L'.join(f'{a} {b}' for a, b in pts[:-1]) + 'Z')
    return ''.join(out)


def proj(x, y, minx, maxy):
    return [round((x - minx) * COS_LAT * SCALE, 1), round((maxy - y) * SCALE, 1)]


def main():
    votes = load_votes()
    cargos = votes.CD_CARGO.unique()
    if len(cargos) > 1:
        votes = votes[votes.CD_CARGO == 1]
    print('cargos:', cargos)

    cands = (votes.groupby(['NR_VOTAVEL', 'NM_VOTAVEL']).QT_VOTOS.sum()
             .reset_index().sort_values('QT_VOTOS', ascending=False))
    nums = [int(n) for n in cands.NR_VOTAVEL]
    candidates = [{'nr': int(r.NR_VOTAVEL), 'nm': r.NM_VOTAVEL, 'votes': int(r.QT_VOTOS)}
                  for r in cands.itertuples()]

    vot_mun = votes.pivot_table(index=['SG_UF', 'CD_MUNICIPIO', 'NM_MUNICIPIO'], columns='NR_VOTAVEL',
                                values='QT_VOTOS', aggfunc='sum', fill_value=0).reset_index()
    vot_uf = votes.pivot_table(index='SG_UF', columns='NR_VOTAVEL', values='QT_VOTOS',
                               aggfunc='sum', fill_value=0).reset_index()

    # --- geometria
    mapa = gpd.read_file(SHP_MUN, encoding='utf-8')
    mapa_uf = gpd.read_file(SHP_UF, encoding='utf-8')
    minx, miny, maxx, maxy = mapa_uf.total_bounds
    width = round((maxx - minx) * COS_LAT * SCALE)
    height = round((maxy - miny) * SCALE)

    # --- merge municípios por UF + nome normalizado
    vot_mun['key'] = [ALIASES.get((u, norm(n)), norm(n)) for u, n in zip(vot_mun.SG_UF, vot_mun.NM_MUNICIPIO)]
    vot_mun['key'] = vot_mun.SG_UF + '|' + vot_mun['key']
    mapa['key'] = mapa.SIGLA + '|' + mapa.NM_MUN.map(norm)
    by_key = vot_mun.set_index('key')

    unmatched_map = mapa[~mapa.key.isin(by_key.index)]
    tse_br = vot_mun[vot_mun.SG_UF != 'ZZ']
    unmatched_tse = tse_br[~tse_br.key.isin(mapa.key)]
    print(f'municípios IBGE sem votos: {len(unmatched_map)}')
    for k in unmatched_map.key:
        print('   IBGE', k)
    print(f'municípios TSE sem geometria: {len(unmatched_tse)}')
    for k in unmatched_tse.key:
        print('   TSE ', k)

    simp_mun = shapely.simplify(mapa.geometry.values, TOL_MUN, preserve_topology=True)
    muns = []
    for (_, row), g in zip(mapa.iterrows(), simp_mun):
        v = by_key.loc[row.key] if row.key in by_key.index else None
        c = g.representative_point()
        muns.append({
            'nm': row.NM_MUN, 'uf': row.SIGLA,
            'd': to_path(g, minx, maxy),
            'c': proj(c.x, c.y, minx, maxy),
            'v': [int(v[n]) if n in v.index else 0 for n in nums] if v is not None else None,
        })

    uf_votes = vot_uf.set_index('SG_UF')
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
            'v': [int(uf_votes.loc[row.SIGLA, n]) for n in nums],
        })

    exterior = [int(uf_votes.loc['ZZ', n]) for n in nums] if 'ZZ' in uf_votes.index else None

    # sanity check contra o notebook (vot_uf AC/13 = 134770)
    if 'AC' in uf_votes.index and 13 in uf_votes.columns:
        print('AC/13 =', int(uf_votes.loc['AC', 13]))

    data = {'w': width, 'h': height, 'candidates': candidates, 'ufs': ufs, 'muns': muns, 'exterior': exterior}
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    with open(TEMPLATE, encoding='utf-8') as f:
        html = f.read().replace('/*__DATA__*/null', payload)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'{OUT}: {os.path.getsize(OUT) / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
