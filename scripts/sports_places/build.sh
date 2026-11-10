#!/bin/sh
# Extrai os locais de esporte do extrato oficial do OpenStreetMap do Brasil
# (Geofabrik) para um GeoJSON por linha. Roda fora do Render, com Docker; depois
# `python -m app.jobs.import_places --context sports --osm-file <dir>/sports.geojsonseq`
# grava os pontos no banco. O extrato baixado é o mesmo dos tiles do Transporte.
#
# Uso: scripts/sports_places/build.sh <diretório de trabalho>
set -eu

WORK=${1:?informe um diretório de trabalho (o extrato tem ~2 GB)}
mkdir -p "$WORK"

if [ ! -f "$WORK/brazil-latest.osm.pbf" ]; then
  curl -fL -o "$WORK/brazil-latest.osm.pbf" \
    https://download.geofabrik.de/south-america/brazil-latest.osm.pbf
fi

docker run --rm -v "$WORK":/data debian:bookworm-slim sh -c '
  set -e
  apt-get update -qq && apt-get install -y -qq osmium-tool >/dev/null
  osmium tags-filter --overwrite -o /data/sports.osm.pbf /data/brazil-latest.osm.pbf \
    nwr/leisure=pitch,skatepark,fitness_station,swimming_pool,track,stadium,sports_centre
  osmium export --overwrite -f geojsonseq -x print_record_separator=false \
    --add-unique-id=type_id -o /data/sports.geojsonseq /data/sports.osm.pbf
  osmium fileinfo -g header.option.osmosis_replication_timestamp /data/brazil-latest.osm.pbf \
    > /data/sports.date
'
