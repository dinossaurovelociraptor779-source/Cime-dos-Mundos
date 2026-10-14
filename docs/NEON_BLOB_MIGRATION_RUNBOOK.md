# Migração segura para armazenamento persistente — Cime dos Mundos 5.0

## Estado desta etapa

Esta branch prepara e testa os pontos de configuração para Neon, mas **a aplicação ainda não está migrada**. O backend atual usa SQLite e operações específicas do SQLite em `server_v5.py` e `server_legacy.py`; apontar essas chamadas diretamente para PostgreSQL quebraria consultas, criação de tabelas e migrações existentes.

Esta etapa não ativa novos backends, não migra dados e não muda a produção.

## Variáveis de ambiente

Configurar no projeto Vercel, sem compartilhar valores no chat:

- `DATABASE_URL`: URL de conexão PostgreSQL do Neon. Preferir a URL pooled quando apropriado ao runtime serverless.
- `BLOB_READ_WRITE_TOKEN`: token do store Vercel Blob.

O módulo `storage_backends.py` expõe somente indicadores booleanos de configuração e uma checagem `SELECT 1`. Ele nunca retorna valores secretos.

## Próximas etapas obrigatórias

1. Inventariar tabelas, índices, constraints, consultas e operações de transação de autenticação, grupos e biblioteca.
2. Criar schema PostgreSQL versionado e testes de migração, preservando IDs e relações.
3. Adaptar a camada de acesso com testes de compatibilidade para consultas e concorrência; não trocar `sqlite3.connect` por psycopg de forma mecânica.
4. Implementar armazenamento de mídia que respeite a privacidade de fotos de grupos; não tornar fotos privadas acessíveis por URLs públicas sem uma decisão explícita de segurança.
5. Criar export/backup verificável do SQLite atual antes de importar. Nunca apagar ou sobrescrever a origem.
6. Migrar em Preview usando uma cópia de dados, comparar contagens e amostras, testar login, biblioteca, progresso, grupos, mensagens e fotos.
7. Fazer teste de persistência após reinicialização e múltiplas instâncias serverless.
8. Somente depois de validação e autorização explícita, planejar ativação em produção e rollback.

## Checagem local

```bash
python -m unittest tests.test_storage_backends -v
```

Os testes não verificam uma conta Neon real nem um store Blob real; essas validações dependem das variáveis configuradas no ambiente de Preview.
