# Geografia (IBGE, território, hidrografia ANA e transporte)

## Território

- `territories`: níveis `country`, `region`, `state` e `municipality`, ligados por `parent_id`, com `ibge_code` único, `abbreviation`, `capital_territory_id`, `bbox_*` e busca sem acento.
- `territory_geometries` (PK `(territory_id, lod)`): `canonical` é a malha do IBGE; `overview` (0,005°) e `detail` (0,001°) são derivadas na ingestão com `ST_SimplifyPreserveTopology`.
- `/map` nunca serve a `canonical` (o padrão é `overview` para país/região e `detail` para UF/município, e municípios exigem `parent`). A `canonical` é usada em `POST /territories/locate` (`ST_Covers`, coordenadas não cacheadas), nas áreas geodésicas e em `/weather/municipal-boundaries` (paginada, até 40 por página).
- Ponto de clima de um município: `ST_PointOnSurface` do `overview`, em ordem de farthest-point sampling a partir da capital.
- Cache do `/map`: o JSON pronto fica em memória por `(nível, pai, LOD, data_version)`, até 64 entradas por 5 min (`READ_CACHE_*`), e no Redis por 24 h. Todas as malhas municipais somam ~17 MB (medido). A resposta leva ETag e `max-age=3600`.

Código: `api/v1/map.py`, `territories.py` · `services/map.py`, `territories.py` · `repositories/territories.py`, `map_projection.py`, `boundaries.py`, `viewport.py`.

## Contas e acompanhamento

A migration `0010_user_accounts` cria `users` (nome, e-mail único normalizado, hash de senha e tema) e `user_sessions` (hash do token, FK da conta e validade). Acrescenta FK de `followed_municipalities.user_id` para `users.id`, mantendo a unicidade por conta/código IBGE. Os usuários legados, inclusive `local`, são preservados sem e-mail/senha e não podem entrar; seus municípios não são atribuídos automaticamente a novos cadastros. A malha e as migrations anteriores ficam inalteradas. Contrato e fluxo de acesso em [architecture](architecture.md).

`0011_notifications` acrescenta `notifications_opt_in_at` aos acompanhamentos e começa com os sinos desligados, incluindo os antigos, sem remover nenhum município seguido. `notification_events` guarda um aviso por conta/aviso/município/versão; `push_subscriptions`, os dispositivos por conta; `push_deliveries`, cada evento/inscrição. FKs removem vínculos ao apagar conta, aviso ou inscrição. A abrangência usa códigos explícitos da fonte ou a malha municipal canônica. Fluxo em [alerts](alerts.md#notificacoes-do-pwa).

## Ingestão IBGE

`python -m app.jobs.bootstrap [--skip-municipal-geometries] [--states 35,31]` roda `import_territories` (Localidades v1) e depois `import_geometries` (Malhas v3; a municipal tem ~60 MB, timeout de 120 s e concorrência 4). É idempotente e grava `ingestion_runs`/`datasets`, que definem o `data_version`. As tolerâncias só mudam na próxima ingestão. Nenhuma requisição de usuário chama o IBGE.

## Indicadores socioeconômicos do IBGE

`0012_socioeconomic` acrescenta `socioeconomic_indicators` (catálogo de 14 indicadores) e `socioeconomic_values` (PK território/indicador/ano, valor decimal, dataset e execução de ingestão). O nome distingue essas tabelas das removidas pela revisão histórica `0009`; nenhuma migration antiga é alterada. Territórios, LODs, contas, notificações e tabelas climáticas são reutilizados sem modificação. `/map` continua aceitando apenas `level`, `parent` e `lod`.

`python -m app.jobs.import_indicators` ingere a série completa da API Agregados v3, com até quatro consultas concorrentes, municípios por UF e publicações preferenciais aplicadas em ordem. O catálogo declarativo está em `providers/ibge/datasets.py`:

| Indicadores | Fontes / regra |
|---|---|
| População; urbana; área | 6579 (estimativas), 4714 (Censo 2022), 202/1301 (2010), 9923 (urbana 2022) |
| PIB; agropecuária; indústria; serviços | 5938, em mil reais convertidos para reais; serviços somam 6575 + 525 |
| Renda domiciliar per capita | PNAD anual 7395, renda mensal real; Brasil/regiões/UFs |
| Desemprego | PNAD 6468, média dos trimestres publicados; 4562 prevalece com média anual oficial; Brasil/regiões/UFs |
| Densidade; PIB per capita; urbanização | Razão com o denominador mais recente do mesmo ano ou anterior, nunca de um ano futuro |
| Crescimento populacional | Variação geométrica anualizada entre observações consecutivas disponíveis |
| Participação no PIB nacional | PIB territorial dividido pelo PIB do Brasil no mesmo ano |

Sentinelas são descartadas, valores ausentes continuam `null` e derivados com denominador zero/ausente não são criados. Catálogo e detalhe exibem os anos realmente presentes em cada nível. A cobertura municipal da PNAD não é extrapolada. Valores não exigem geometria: município com dado e sem malha permanece no detalhe, mas não pode ser pintado.

Quantis de dez classes são calculados sobre todos os territórios do mesmo nível/indicador/ano, inclusive quando se pede uma UF: uma cor municipal mantém o mesmo significado entre UFs. `classification` informa `scope: national`, limites e quebras; `statistics` descreve somente o recorte pedido. Ao preparar o mosaico municipal nacional, o frontend mantém a legenda estadual até a publicação do desenho municipal. Quebras repetidas são mantidas para preservar as dez cores da escala; valores iguais continuam na mesma classe e algumas faixas podem ficar sem territórios. Ausência de todos os valores continua sem classificação. A classificação não indica risco nem juízo de valor.

A importação tem uma transação para todas as fontes e derivados; uma falha aborta e preserva o snapshot anterior. `--latest` limita cada fonte ao seu último ano (não elimina histórico existente); `--periods 2021|2022` seleciona períodos publicados; `--states 35,31` restringe apenas os municípios e mantém os agregados nacionais; `--indicators population,gdp` limita indicadores/dependências; `--skip-municipalities` limita a carga a Brasil/regiões/UFs. A prioridade censitária e da PNAD anual é preservada. Não há ingestão no request nem polling de fonte socioeconômica no agendador climático.

## Indicadores do Meio ambiente (PRODES e MapBiomas)

`0014_environment_indicators` acrescenta `context` ao catálogo (`socioeconomic` | `environment`) e os indicadores `deforestation_prodes` (km², 2 casas) e `forest_cover` (%, 1 casa). Os valores ficam em `socioeconomic_values`, como os do IBGE ([ADR-15](decisions.md)).

`python -m app.jobs.import_environment` lê:
- **PRODES/INPE** (`providers/prodes.py`): para cada um dos seis biomas, o JSON de áreas do painel do TerraBrasilis e o catálogo de municípios com `codibge`. Só períodos de um ano entram (os bienais e o acumulado até 2000/2007 ficam de fora). A soma por município cobre os biomas em que ele aparece, só nos anos que todos os biomas publicaram; município listado sem área no ano vale 0 km² medidos. `last_update_date.json` vira `source_updated_at` do dataset.
- **MapBiomas Coleção 11** (`providers/mapbiomas.py`): aba `COVERAGE_11` do xlsx municipal, lida em streaming. Floresta = nível 1 `1. Forest`; o denominador é a soma de todas as classes do município no ano. Linhas sem `geocode` de 7 dígitos (o rodapé e as lagoas) são descartadas. Anos 1985, 1990, …, 2020 e 2021 em diante.

UF, região e Brasil somam os municípios da fonte pelo prefixo do código IBGE (floresta: soma de hectares de floresta sobre soma de área, nunca média de percentuais). A transação é única: falha de uma fonte preserva a série anterior. Testes: `test_environment.py`, sem rede (fixtures de JSON e um xlsx mínimo gerado no teste).

## Alertas de desmatamento (DETER/INPE)

Aba **Alertas** do Meio ambiente ([ADR-20](decisions.md)). Sob demanda, sem persistência, como os focos. Código: `providers/deter.py`, `services/deforestation_alerts.py`, `api/v1/deforestation_alerts.py`. Testes: `test_deforestation_alerts.py`, sem rede.

- `GET /deforestation-alerts` devolve a janela (`windowStart`/`windowEnd`, 14 dias), o total e os km², os municípios e UFs com alerta (`alertKm2`, `count`, `density` = km² × 1000 / área do município, área geodésica da malha canônica, a mesma dos focos), os cinco maiores e **os pontos** (centro do polígono de cada alerta, com `ibgeCode` para o frontend filtrar por estado, ~250 KB). Só entram municípios com alerta; os demais ficam sem valor.
- Fonte: WFS do TerraBrasilis (`deter-amz:deter_amz` e `deter-cerrado-nb:deter_cerrado`), `view_date >= hoje − 30 dias` e `classname IN (DESMATAMENTO_CR, DESMATAMENTO_VEG, MINERACAO)`, as classes que o painel do INPE soma como desmatamento. O fim da janela é o **último `view_date` publicado**, não hoje: o INPE publica com ~2 semanas de atraso (em 30/10/2026 o último era 16/10), e uma janela de 48 h ficaria vazia.
- O município vem de `mun_geocod` (Amazônia). O DETER do Cerrado só traz nome e UF: o casamento é por nome sem acento e UF contra a malha; quem não casa entra em `unassignedCount`.
- Cache: 2 h em memória e no Redis, chave com o dia e a `data_version`; lock por chave, falha memorizada por 60 s e `deter_cooldown` de 60 s. A rota responde `Cache-Control: public, max-age=600`.
- Limites: só Amazônia e Cerrado (os outros biomas não têm alerta diário); é alerta de fiscalização, não a taxa do PRODES, e a resposta carrega `notice` com isso. Consulta ordenada por `view_date` leva ~22 s na fonte (sem índice): por isso a janela sai de um filtro, nunca de `sortBy`.

## Hidrografia (ANA/SNIRH)

`GET /hydrography?zoom=&bbox=` (`services/hydrography.py`). Só o zoom e a área definem a resposta; a rota não usa o banco.
- **Zoom < 6:** rios do snapshot `services/data/major_rivers.json`, filtrados por área de drenagem, e lagos grandes da ANA no recorte do Brasil. O bbox é ignorado: uma só entrada de cache para qualquer enquadramento, aquecida no `lifespan`.
- **Zoom ≥ 6:** ArcGIS REST do SNIRH (rios e massas d'água) na área pedida, com o detalhe definido por `hydro_detail(zoom)`. Rios e lagos saem em paralelo, assim como os lotes de 250 feições, com no máximo 4 conexões com a ANA.
- **Peso:** os trechos de cada rio são emendados (`_merge_lines`) e depois simplificados e recortados. Anéis de lago, ilha ou buraco com área menor que `(4 × tolerância)²`, poucos pixels no detalhe, são descartados (`_visible_rings`). Nas escalas nacional e regional, isso reduz a resposta a menos da metade.
- **Área pedida:** o frontend encaixa o bbox numa grade (1° no zoom 6–8, 0,5° no 8–10, 0,25° acima), reaproveita qualquer área já carregada que cubra a vista e não manda bbox abaixo do zoom 6.
- **Cache:** chave `(detalhe, área)`, 24 h em memória (como JSON pronto) e no Redis, com HTTP `max-age` de 1 h. Se a ANA falha, entra o cooldown de 60 s, os rios caem para o snapshot, os lagos saem e a resposta vira `partial`, com cache de só 60 s (sem Redis, e o mesmo `max-age`).
- Testes: `test_hydrography.py`.

## Transporte (DNIT e IBGE)

`0015_transport` cria `transport_features` (camada, nome, atributos em JSONB, geometria simplificada e dataset), `transport_overviews` (o traçado nacional fundido de federais, ferrovias e hidrovias) e `transport_totals` (extensão em km por território e camada; portos por contagem). Decisão e alternativas no [ADR-17](decisions.md).

`python -m app.jobs.import_transport` lê:
- **SNV do DNIT** (`providers/dnit.py`): a versão mais recente de `vgeo:vw_snv_rod`, em páginas de 2.000 trechos ordenadas por `id_snv`. A superfície vira texto (`DUP` → "Duplicada", `PAV`, `EOD`, `EOP`, `IMP`, `LEN`); `PLA`, `EOI` e `TRV` ficam de fora. Nome `BR-xxx`, código SNV, administração e trecho (`Local_Inicio – Local_Fim`).
- **BC250 2025 do IBGE** (`providers/bc250.py`, com `providers/shapefile.py` lendo Point/PolyLine e DBF pela biblioteca padrão): `rod_trecho_rodoviario_l` (estaduais = `Estadual/Distrital`; locais = municipais, desconhecidas e sem jurisdição), `fer_trecho_ferroviario_l`, `hdv_trecho_hidroviario_l` e `hdv_complexo_portuario_p`. Federais e propriedades particulares do BC250 são descartadas, assim como o que não está construído. Valores desconhecidos (`Desconhecida`, `Não aplicável`, `Outros`) não viram atributo. Das locais só se guarda o revestimento.

Na mesma transação: trechos simplificados a 0,0003° (preservando trechos curtos), traçado nacional por camada (`ST_LineMerge` das partes e simplificação a 0,01°) e extensão por município (`ST_Intersection` com a malha `detail`, comprimento geodésico), somada por prefixo do código IBGE. Porto conta para o município mais próximo até 0,05°. Medido em 2026-10-26: 5.336 federais, 34.450 estaduais, 74.019 locais, 464 ferrovias, 134 hidrovias e 130 portos; Brasil com 82,3 mil km de federais, 238,9 mil de estaduais, 26,2 mil de ferrovias e 35,4 mil de hidrovias. Testes: `test_transport.py`.

### Tiles OSM do Transporte

`0016_transport_tiles` cria `transport_tiles` (zoom, x, y, tile MVT em gzip), na numeração XYZ do mapa. `scripts/transport_tiles/build.sh <dir>` gera o `.mbtiles` com Docker: baixa o extrato do Brasil da Geofabrik, mantém só `highway`, `railway`, estações e `route=ferry` (`osmium tags-filter`), recorta pela fronteira em `brazil-border.poly` (união das UFs do IBGE com 500 m de margem; `osmium extract --strategy complete_ways`, porque com `simple` a via que cruza a fronteira perde nós e o Planetiler a descarta, abrindo buracos nas pontes) e roda o Planetiler com `transport.yml` e `--polygon` no mesmo arquivo, que limita a saída aos tiles que tocam o Brasil. `python -m app.jobs.import_transport_tiles --mbtiles <dir>/transport.mbtiles` grava os lotes em `transport_tiles_next` (commit e nova tentativa por lote; rodar de novo retoma o mesmo arquivo) e troca para `transport_tiles` numa transação curta dentro do banco; inverte a linha do TMS para XYZ e guarda a data do extrato (`osm_date`) na execução. Camadas e zooms em [ADR-18](decisions.md); vias com sigla federal (BR-) entram no zoom 4 e com qualquer outra sigla no 5, antes da classe delas, e ferrovias principais no 4. Testes: `test_transport.py`.

### Municípios eleitorais

Política usa o [de/para oficial TSE–IBGE](https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip). O código municipal do TSE não é código IBGE nem permite deduzir a UF por prefixo. A ingestão normaliza somente o preenchimento numérico do identificador e exige correspondência explícita com um território já ingerido; município brasileiro sem correspondência aborta a publicação. As tabelas finais referenciam `territories.ibge_code` e reutilizam todas as geometrias/LODs e o mosaico existentes.

Registros do exterior e em trânsito sem município da malha são excluídos e contabilizados na proveniência. Por isso o resumo Brasil é a soma dos municípios cobertos, podendo divergir do total nacional do TSE que inclui o exterior. Ausência de eleição municipal no DF/Fernando de Noronha ou de segundo turno numa localidade fica sem dados. A malha é a atual do IBGE, não uma reconstrução histórica; a interface explicita essa limitação.
