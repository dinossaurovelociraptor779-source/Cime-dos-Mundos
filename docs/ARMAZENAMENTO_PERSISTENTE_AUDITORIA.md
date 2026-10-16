# Cime dos Mundos 5.0 — Auditoria de armazenamento
Data: 2026-10-16

## 1.1 Inventário do armazenamento atual

### Backend / Vercel
- `api/index.py` define `CIME_DATA_DIR=/tmp/CimeDados`.
- `server_v5.py` guarda autenticação em SQLite (`cime_auth.db`), incluindo usuários, sessões, feedback, estados OAuth, grupos, membros, convites, recomendações, fotos, mensagens e links de compartilhamento.
- A biblioteca de cada usuário fica em arquivos SQLite separados (`users/<id>/library.db`), com mídia em diretórios locais por usuário.
- `server_legacy.py` usa SQLite (`cime.db`) para títulos, progresso/histórico, importações e caches; também grava mídia localmente.
- `/tmp` na Vercel é temporário por instância e não pode ser tratado como persistência durável.
- Não foi encontrado adaptador do backend que grave os dados da aplicação diretamente no Supabase.

### Entidades SQLite identificadas
- Autenticação: `users`, `sessions`, `feedback`, `oauth_states`.
- Grupos: `groups`, `group_members`, `group_invites`, `group_recommendations`, `group_photos`, `group_messages`, `group_share_links`.
- Biblioteca: `titles`, `watch_history`, `imported_history`.
- Cache: `anime_cache`, `ai_research_cache`, `ai_vision_cache`, `ocr_cache`.
- O esquema exato pode variar com migrações SQLite executadas em runtime.

## 1.2 Backup — estado e limitação
- As sete tabelas funcionais verificadas no Supabase estavam vazias: `profiles`, `library_items`, `memories`, `groups`, `group_members`, `group_messages`, `recommendations`.
- O bucket `cime-media` estava privado e sem objetos listados.
- Isso não é um backup dos arquivos SQLite da Vercel. Não foi obtida uma cópia consistente dos arquivos SQLite de produção pelo acesso disponível.
- Não apagar nem migrar o banco antigo antes de haver uma exportação consistente e uma restauração testada.

## 1.3 Supabase — estado verificado
- Tabelas: `profiles`, `library_items`, `memories`, `groups`, `group_members`, `group_messages`, `recommendations`.
- As tabelas usam UUIDs e várias FKs apontam para `auth.users`; o backend atual usa autenticação própria SQLite com IDs inteiros. Essa incompatibilidade precisa ser resolvida antes da migração.
- Bucket `cime-media`: privado, limite de 50 MiB, MIME permitidos JPEG, PNG, WebP, GIF, MP4 e WebM.
- Corrigidas políticas RLS que comparavam `gm.group_id = gm.group_id`, permitindo que a checagem de participação em grupo não estivesse vinculada ao grupo da mensagem/recomendação.
- Adicionados índices em `group_messages.user_id`, `groups.owner_id`, `recommendations.group_id` e `recommendations.source_item_id`.
- O advisor de segurança não reportou alertas após a correção. O advisor de desempenho ainda lista índices não utilizados, esperado num banco vazio; não remover índices sem dados de uso.

## Bloqueios antes de 1.4–1.7
1. Definir e implementar uma estratégia única de persistência. O backend usa autenticação SQLite; as tabelas Supabase atuais pressupõem Supabase Auth.
2. Obter exportação consistente do SQLite legado e dos arquivos de mídia. Isso não foi concluído nesta auditoria.
3. Confirmar uma credencial de banco apropriada no ambiente sem expor segredos. Variáveis de integração de banco presentes na Vercel não provam que o backend atual as utiliza.
4. Implementar e testar migração numa cópia antes de apontar produção para o novo armazenamento.
5. Testar CRUD, logout/login, nova instância e nova implantação.

## Regra de segurança
Não registrar tokens, hashes de senha, cookies, chaves privadas ou valores de variáveis secretas neste documento.
