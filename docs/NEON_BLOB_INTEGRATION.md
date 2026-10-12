# Cime dos Mundos 5.0 — integração Neon + Blob (branch isolada)

## Estado confirmado no código-base

- O backend `server_v5.py` usa SQLite para autenticação e bibliotecas individuais.
- `api/cime.py` e `api/index.py` definem `CIME_DATA_DIR=/tmp/CimeDados`, que não é armazenamento persistente na Vercel.
- O fluxo de arquivos/grupos usa caminhos locais e precisa ser adaptado para Blob sem quebrar os caminhos locais usados no desenvolvimento.
- `vercel.json` encaminha `/api/:path*` para o gateway e possui redirects de `/app` para `/app.html`; a rota `/app.html` precisa de teste HTTP real na implantação.
- O driver PostgreSQL foi adicionado às dependências nesta branch; isso, sozinho, **não** migra o banco nem faz o backend usar Neon.

## Sequência obrigatória antes de produção

1. Mapear todas as tabelas SQLite, índices, chaves estrangeiras e consultas específicas de SQLite.
2. Implementar camada de persistência PostgreSQL compatível com o código existente e migrações idempotentes.
3. Implementar migração de dados com modo de simulação, contagens antes/depois e sem apagar a origem.
4. Armazenar fotos/arquivos no Vercel Blob; persistir no banco apenas identificadores/URLs e metadados, respeitando o acesso por usuário/grupo.
5. Criar testes automatizados para autenticação, isolamento por conta, biblioteca, progresso, grupos, memórias, upload e rotas.
6. Executar testes em Preview com dados de teste. Só depois promover para produção.

## Segurança / não fazer

- Não registrar tokens, senhas, connection strings ou conteúdo de variáveis secretas nos logs.
- Não apagar nem substituir bancos SQLite durante a migração.
- Não publicar esta branch na URL principal até os testes passarem.
- Não declarar testes de ponta a ponta aprovados sem executá-los contra um deployment real.
