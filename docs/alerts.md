# Alertas (INMET e CEMADEN)

Alertas oficiais em vigor, gravados na tabela `weather_alerts`. Um agendador os ingere a cada 10 min (`jobs/weather_scheduler.py`, desligável com `WEATHER_REFRESH_ENABLED=false`) e `GET /weather/alerts` os lê do banco, com 90 s de cache do JSON pronto, ETag e `Cache-Control: no-cache`: o polling do frontend recebe `304` vazio enquanto nada muda. Os polígonos saem com 5 casas decimais (~1 m); as fontes mandam 8, o que dobrava a resposta. Um ciclo que falha é registrado em `ingestion_runs` e não apaga nada; `/weather/sources` mostra se cada job está `stale` (passou de 3 intervalos) ou `unavailable`.

Código: `providers/inmet/alerts.py`, `providers/cemaden/alerts.py`, `jobs/_weather_alerts.py` (upsert em lote), `services/weather.py` (normalização).

## Notificações do PWA

O sino de cada município autoriza notificações do app. Novos acompanhamentos começam com `notifications_enabled=false`; ativar grava `notifications_opt_in_at`, desativar limpa a autorização. A migration `0011_notifications` preserva os municípios seguidos, mas desativa os sinos antigos, que antes não entregavam mensagens. O cadastro informa que os avisos são opcionais; não pede a permissão do navegador nem condiciona a conta a ela. O clique no sino ou em “Ativar neste dispositivo” pede essa permissão e registra uma inscrição Web Push vinculada à conta. É possível revogar um município ou desativar só o dispositivo, preservando os demais. Sair pelo aplicativo remove a inscrição do dispositivo e fecha suas notificações.

`services/notifications.py` separa duas responsabilidades: criar `notification_events` com o conteúdo do aviso e formatar/enviar pelo canal Web Push. Os eventos contêm município, fonte, evento, severidade, descrição, riscos e instruções; não contêm nome/e-mail da conta nem detalhes de transporte. Há uma versão por conta/aviso/município/conteúdo. Mudanças de evento, severidade, descrição, riscos ou instruções geram nova versão. Renovar só a validade artificial do CEMADEN não gera outro aviso. Isso permite acrescentar e-mail no futuro consumindo os mesmos eventos, com formatação, entregas e autorização próprias, sem reescrever a abrangência territorial. Não existe implementação ou envio de e-mail nesta versão.

Só entram avisos vigentes e municípios com sino autorizado. A lista explícita de códigos IBGE da fonte prevalece; quando vazia, `ST_Intersects` cruza o polígono com a malha canônica municipal, nunca apenas bbox. `push_deliveries` registra cada evento por inscrição/dispositivo, com unicidade, aceitação, tentativas e próxima execução. Antes de enviar, o job verifica autorização, vigência, abrangência e versão novamente. Autorizar durante um aviso vigente ou adicionar outro dispositivo permite recebê-lo naquele dispositivo. Desativar o sino ou deixar de seguir impede próximas tentativas; uma mensagem já aceita pelo serviço de push pode chegar depois da revogação.

O adaptador `providers/push.py` usa `pywebpush` para criptografia Web Push padrão e assinatura VAPID por HTTPS, numa thread. A inscrição tem endpoint e chaves do navegador; os endpoints aceitos ficam nos serviços Google, Mozilla, Apple e Microsoft, para impedir requisições a URLs arbitrárias/endereços internos. A API exige sessão e proteção de escrita nas inscrições, que não podem ser removidas por outra conta. Trocar a conta de um endpoint remove o vínculo e as entregas anteriores. Respostas 404/410 eliminam inscrições expiradas. Erros de rede/5xx retomam em ciclos futuros (10/20/40 min, até 1 h, máximo de cinco tentativas); 400 encerra a entrega inválida; 401/403/429 ou configuração VAPID inválida interrompe o ciclo até corrigir configuração ou renovar limite, sem consumir tentativas. Logs não incluem endpoint, chaves ou resposta original do provider.

Um advisory lock no Postgres coordena cron e API, com uma transação por envio. O limite é 500 entregas por ciclo (reduzível por `NOTIFICATION_BATCH_LIMIT`). O TTL enviado ao serviço não ultrapassa a validade do aviso nem 1 h. `Topic` e a tag visível da notificação são estáveis por evento, reduzindo duplicatas em novas tentativas. `sent_at` significa aceitação pelo serviço, não leitura ou confirmação no dispositivo; Web Push não oferece garantia de exatamente uma entrega. O worker descarta avisos já expirados e o clique abre o município no app, quando ele continua na lista seguida.

`public/sw.js` no frontend existe apenas para push/click: não intercepta `fetch`, não tem cache offline e não altera o frescor dos dados. HTTPS e navegador compatível são necessários; no iOS/iPadOS, é preciso instalar na Tela de Início (16.4+). O usuário autoriza cada dispositivo por uma ação explícita; abrir/cadastrar não pede permissão. A entrega depende também da rede e das permissões do sistema.

Com `PUSH_NOTIFICATIONS_ENABLED=true` e chaves configuradas, o agendador da API executa o envio. `.github/workflows/notifications.yml` ingere INMET/CEMADEN e despacha a cada 30 min mesmo com o Render dormindo. Atrasos do GitHub e limitações gratuitas impedem prometer envio instantâneo ou disponibilidade contínua. Não substitui os canais oficiais da Defesa Civil. Configuração: [development](development.md#notificacoes-do-pwa).

Testes: `test_notifications.py` e `test_notification_migration.py`, sem push real, cobrem autorização/revogação, troca de conta, múltiplos dispositivos, abrangência, expiração, deduplicação e novas tentativas. O frontend testa permissão, inscrição/cancelamento, worker sem cache e abertura do município.

## Modelo unificado

- `category` vem do provider: o CEMADEN é `geo_hydrological`, o resto `meteorological`.
- `severityLevel` (`moderate`, `high`, `very_high`, `extreme`) é derivado do texto original, que continua em `severity`:
  - INMET: "grande perigo"/"extremo" → `extreme`; "muito alto" → `very_high`; "potencial"/"moderado" → `moderate`; "perigo"/"alto" → `high`.
  - CEMADEN: "muito alto" → `very_high`; "alto" → `high`; o resto → `moderate`.
- O enum ainda declara `potential` e `danger`, que nunca são emitidos: é legado.

## INMET

`GET {INMET_ALERTS_BASE_URL}/avisos/ativos`, lista `hoje`. Avisos `encerrado`, sem id, sem datas ou sem polígono são descartados. O polígono pode vir como string e é corrigido com `ST_MakeValid`. A validade é o próprio `data_fim`, então, se a fonte cai, os avisos gravados continuam valendo até lá. Testes: `test_providers_inmet.py`, com um payload montado a partir dos campos que o parser lê (não é uma captura real da API); se a fonte mudar o formato, refaça-o com uma resposta real. Severidade, categoria e status das fontes: `test_weather_sources.py`.

Estações (`providers/inmet/stations.py`, job manual, `/weather/stations`): o agendador não roda esse job e o frontend não consome a rota. Integre ou remova antes de investir nelas.

## CEMADEN

WFS `GetFeature` em `cemaden_dev:alertas_vigentes_siaden` com `status=1`. Feições com outro status são descartadas mesmo que o filtro falhe. O evento perde o sufixo " - {nivel}"; `codibge` vira `affected_ibge_codes`; o PDF vira uma instrução. Como a fonte não informa validade, cada ciclo grava `expires = agora + 30 min` (`CEMADEN_ALERT_VALIDITY_BUFFER_SECONDS`) e o alerta some sozinho quando sai da lista ou a ingestão para. Não troque isso por "nunca expira". Testes: `test_providers_cemaden.py`.
