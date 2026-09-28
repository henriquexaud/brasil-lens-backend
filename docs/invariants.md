# Invariantes

Regras que só podem quebrar por decisão explícita: atualize este arquivo e registre o motivo em [decisions.md](decisions.md). O teste citado protege a regra. Os invariantes próprios do frontend estão em `frontend/AGENTS.md`.

1. **Medido × estimado.** Toda cidade interpolada sai com `isInferred: true`; estimativa nunca aparece como medição. `services/spatial_interpolation.py` · `test_spatial_interpolation.py`
2. **Dado velho nunca parece atual.** Uma leitura de clima é usável até 2 h. Entre 2 h e 12 h, só quando a fonte falha, e com `status: stale`. Depois de 12 h, nunca. Focos em fallback saem `stale`; hidrografia degradada sai `partial`. · `test_readings_beyond_twelve_hours_are_never_presented`, `test_fallback_preserves_timestamp_and_is_marked_stale`
3. **Ausência não vira zero.** Valor faltante continua `null`: nada de 0 mm, 0 °C ou "sem risco". Densidade sem área válida também é `null`. · `test_missing_temperature_is_not_fabricated`, `test_missing_measurements_are_not_zero_or_false_risk`
4. **Janela de 48 h** para chuva acumulada e para focos. Mudar exige ajustar backend, frontend e testes juntos. · `test_rain_accumulation_has_48_hourly_values_and_preserves_missing_data`
5. **Uma janela só para os focos.** Contagem, WMS, resumo e identificação saem de `fire_scope.time_filter`; resumo e identificação usam o `at` da camada (no máximo 2 h). · `test_identify_keeps_window_and_orders_points_by_distance`
6. **Frescor espelhado.** Território selecionado: 15 min. Mapa: 30 min. Mínimo entre buscas: 2 min. As mesmas constantes existem em `frontend/src/features/weather/queries.ts`. · `test_fresh_reading_needs_no_fetch_and_expired_one_renews_in_background`
7. **Cota da Open-Meteo.** Lotes de até 100 locais, dedup em voo e cooldown que respeita `Retry-After`. O viewport mede no máximo 80 pontos; nenhuma rota consulta um ponto por município sem amostragem. · `test_concurrent_requests_share_one_fetch`, `test_rate_limit_stops_all_outbound_calls_and_serves_fallback`
8. **Amostra estável.** Os pontos de uma UF seguem farthest-point sampling a partir da capital (`territories.list_weather_points`), e os 20 primeiros são os medidos. · `test_state_sample_is_reused_by_the_close_view`
9. **Falha isolada.** Enquanto uma fonte está em cooldown, nenhuma chamada sai para ela, e as outras rotas seguem normais. · `test_inpe_outage_pauses_every_scope_and_says_when_it_retries`
10. **Alerta ativo = `expires > now()`.** O CEMADEN não informa validade, então cada ciclo grava `agora + 30 min`: se a ingestão para, os alertas somem sozinhos. Upsert por `(provider, external_id)`. · `test_providers_cemaden.py`
11. **O código IBGE é a chave universal.** Todas as fontes se juntam por ele, e recortes são validados por código, nunca só por bbox. · `test_scope_uses_ibge_codes_not_just_bbox`
12. **Derivados da geografia seguem `data_version`.** · `test_geography_cache.py`, `test_new_ingestion_rebuilds_national_weights_and_grid`
13. **Redis é opcional.** Sem ele, tudo continua funcionando só com o cache local. · `test_cache_connection_failure_or_invalid_value_returns_miss`
14. **Erros e contratos estáveis.** O formato de erro e os `code` não mudam de significado; o frontend depende de `provider_rate_limited`. Um campo renomeado quebra o frontend em silêncio. · `test_rate_limited_endpoint_explains_the_cause_to_the_client`, `test_rain_fields_keep_the_frontend_names`
15. **Migrations são append-only.**
