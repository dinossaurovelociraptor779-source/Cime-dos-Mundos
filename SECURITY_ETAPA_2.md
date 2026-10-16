# Etapa 2 — Segurança, login e separação de contas

## Alterações propostas nesta branch

- Remove o segredo fixo conhecido usado para assinar sessões.
- Em Vercel, não emite nem aceita tokens stateless se não houver `CIME_SESSION_SECRET` ou `GOOGLE_CLIENT_SECRET` configurado.
- Serializa com `BEGIN IMMEDIATE` a decisão de qual conta pode receber o papel inicial de criador, incluindo cadastro local e fluxos Google.
- Inclui testes de regressão para hash de senha, assinatura/tampering de sessão e caminhos de bancos por usuário.

## Configuração obrigatória antes do deploy

Defina na Vercel uma variável de ambiente **CIME_SESSION_SECRET** com um valor aleatório forte (pelo menos 32 bytes aleatórios), em Production e Preview conforme necessário. Não use o valor de teste nem publique o segredo no GitHub. A configuração atual usa `GOOGLE_CLIENT_SECRET` como compatibilidade se `CIME_SESSION_SECRET` estiver ausente; o segredo dedicado é preferível.

## Bloqueadores que impedem declarar a Etapa 2 concluída

1. A versão Vercel atual grava autenticação e bibliotecas em `/tmp/CimeDados`. Esse diretório não é armazenamento persistente/compartilhado entre instâncias. O fallback de sessão stateless permite que o login sobreviva a instâncias diferentes, mas significa que logout/revogação não pode ser garantido de forma confiável sem um armazenamento de sessão/revogação persistente.
2. A separação da biblioteca local é feita por arquivos SQLite por usuário. Em Vercel, os arquivos em `/tmp` podem desaparecer e não são uma base compartilhada. Para isolamento confiável entre instâncias e persistência, migrar usuários, sessões e dados privados para um banco persistente (por exemplo PostgreSQL/Supabase) antes de tratar o beta online como seguro.
3. Os testes adicionados precisam ser executados em ambiente com as dependências do projeto; o branch ainda deve passar por build/deploy Preview e teste manual com duas contas antes de merge.

**Não mesclar nem anunciar “100% seguro” antes de resolver os bloqueadores acima.**
