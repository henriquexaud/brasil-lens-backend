#!/bin/sh
# Gera os tiles vetoriais do Transporte (ADR-18) a partir do extrato oficial do
# OpenStreetMap do Brasil (Geofabrik), só com transporte e recortados à
# fronteira do IBGE. Roda fora do Render, com Docker; depois o
# `python -m app.jobs.import_transport_tiles --mbtiles <dir>/transport.mbtiles`
# grava os tiles no banco.
#
# Uso: scripts/transport_tiles/build.sh <diretório de trabalho>
set -eu

WORK=${1:?informe um diretório de trabalho (precisa de ~6 GB livres)}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$WORK"

if [ ! -f "$WORK/brazil-latest.osm.pbf" ]; then
  curl -fL -o "$WORK/brazil-latest.osm.pbf" \
    https://download.geofabrik.de/south-america/brazil-latest.osm.pbf
fi

# Fronteira do IBGE (união das UFs, com 500 m de margem para pontes e litoral):
# o extrato da Geofabrik tem folga e traz vias dos países vizinhos. A malha do
# país na API do IBGE é grosseira demais (3,4 mil pontos) para esse recorte.
cp "$HERE/brazil-border.poly" "$WORK/brazil.poly"

# Só as etiquetas de transporte, e depois o recorte. Uma via que cruza a
# fronteira vem inteira ("complete_ways"): cortá-la no último nó dentro do
# Brasil ("simple") deixa a via sem nós e o Planetiler a descarta, abrindo
# buracos nas pontes de fronteira. O Planetiler limita os tiles ao polígono.
docker run --rm -v "$WORK":/data debian:bookworm-slim sh -c '
  set -e
  apt-get update -qq && apt-get install -y -qq osmium-tool >/dev/null
  # O cabeçalho de replicação vira a data do extrato no catálogo (osm_date).
  osmium tags-filter --overwrite --output-header=osmosis_replication_timestamp! \
    -o /data/transport-tags.osm.pbf /data/brazil-latest.osm.pbf \
    w/highway w/railway n/railway=station,halt w/route=ferry
  osmium extract --overwrite --strategy complete_ways -p /data/brazil.poly \
    --output-header=osmosis_replication_timestamp! \
    -o /data/brazil-transport.osm.pbf /data/transport-tags.osm.pbf
'

cp "$HERE/transport.yml" "$WORK/transport.yml"
docker run --rm -v "$WORK":/data ghcr.io/onthegomap/planetiler:latest generate-custom \
  --schema=/data/transport.yml --output=/data/transport.mbtiles --minzoom=4 --maxzoom=13 \
  --polygon=/data/brazil.poly --force
