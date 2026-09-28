# Decisões (ADRs)

Cada entrada registra o que foi decidido, por quê e qual o custo. Para uma decisão nova com alternativas reais (fonte, cache, contrato, persistência, estrutura), acrescente `ADR-NN` no fim. Se ela substituir uma anterior, marque a antiga como "Substituída por ADR-NN". As entradas de 01 a 08 registram, em 2026-09-28, decisões que já estavam no código.

**ADR-01 — A API própria é a porta dos dados.** Normalização, cota, cache e fallback ficam num lugar só. O navegador acessa terceiros apenas para tiles (Esri; WMS do INPE com URL e filtro vindos da API). *Custo:* a API precisa de cache e degradação. Qualquer nova chamada direta do navegador passa por revisão desta decisão.

**ADR-02 — Geografia no PostGIS, com LODs e versão.** A malha municipal tem ~60 MB, então `overview` e `detail` são pré-calculados e `/map` serve só esses. A `canonical` fica para `ST_Covers`, áreas e o contorno fino. `data_version` versiona todos os derivados. *Custo:* esquecer `data_version` num cache novo serve geografia velha, e trocar a tolerância exige ingerir de novo.

**ADR-03 — Cache em memória + Redis opcional.** O Redis compartilha e sobrevive a restarts, mas nunca é obrigatório: se falha, conta como miss. *Custo:* cooldowns e dedup valem por processo. Hoje roda 1 processo; com vários workers, isso muda.

**ADR-04 — Clima sob demanda, com amostragem e interpolação.** São 5.570 municípios contra ~10 mil chamadas/dia. A solução é consultar por ponto, com cache de leitura, amostrar por escala e estimar o resto marcado com `isInferred`. *Custo:* a interpolação suaviza extremos locais, e a ordem da amostra vira contrato.

**ADR-05 — Alertas ingeridos no banco.** São poucos, valem para o país inteiro e as fontes são lentas e instáveis. Por isso um agendador os grava a cada 10 min e a rota lê do banco. *Custo:* o agendador roda dentro da API; com várias instâncias, desligue nas extras (`WEATHER_REFRESH_ENABLED=false`).

**ADR-06 — Focos: pontos por WMS e números pela API, na mesma janela.** Dezenas de milhares de pontos não cabem em GeoJSON. O WMS desenha os pontos, a API agrega, e todos usam `fire_scope.time_filter`. *Custo:* o SLD fica no frontend, limitado a ~5 KB, e a consistência depende de conferir o total antes e depois da paginação.

**ADR-07 — Degradação explícita e isolada por fonte.** Há cooldown por fonte e fallback com limite de idade, marcado `stale`/`partial`, ou então um erro com `code` estável. O frontend pausa só aquela fonte. *Custo:* todo payload externo carrega um estado que a interface precisa mostrar.

**ADR-08 — Frontend e backend em repositórios separados.** O backend guarda a documentação de sistema e o Compose completo; o frontend documenta só o próprio lado. *Custo:* mudança de contrato exige dois commits coordenados, e os links `../backend/...` não resolvem no GitHub. Se isso começar a doer, reavalie um monorepo, o que também resolveria a colisão de nome de projeto no Compose.
