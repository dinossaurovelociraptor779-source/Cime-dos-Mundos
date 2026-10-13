# Configuração de segurança da produção — Cime dos Mundos 5.0

Esta alteração é deliberadamente fail-closed: o backend não inicia corretamente em produção se faltar uma chave de sessão segura. Configure as variáveis **antes de fazer merge/deploy**.

## Variáveis obrigatórias na Vercel

- `CIME_SESSION_SECRET`: valor aleatório, único para este ambiente, com pelo menos 32 caracteres. Use um gerador confiável de segredos; não reutilize a senha do Google nem a chave OAuth. Não coloque o valor no GitHub, em mensagens ou em capturas de tela.
- `CIME_CREATOR_EMAILS`: lista de e-mails autorizados a receber o perfil `creator`, separados por vírgula. Use somente o e-mail da conta proprietária. Não inclua e-mails de testadores.

Configure-as em Vercel → Project → Settings → Environment Variables para os ambientes que serão usados (Production e Preview, se aplicável). Não é necessário compartilhar os valores com quem estiver revisando o código.

## Comportamento após a alteração

- Em produção, o backend rejeita inicialização se `CIME_SESSION_SECRET` estiver ausente ou tiver menos de 32 caracteres.
- O segredo da sessão não usa mais o segredo OAuth do Google como fallback.
- Em produção, uma conta nova só recebe `creator` se seu e-mail estiver em `CIME_CREATOR_EMAILS`; as demais começam como `beta`.
- Em desenvolvimento local, o primeiro usuário ainda pode ser `creator`. O segredo local temporário pode invalidar sessões quando o processo reinicia.
- Contas existentes mantêm a função gravada no banco, se esse banco continuar disponível.

## Antes do merge/deploy

1. Faça uma cópia de segurança da pasta local `CimeDados`, se ela existir.
2. Confirme que `CIME_SESSION_SECRET` e `CIME_CREATOR_EMAILS` estão configuradas sem revelar seus valores.
3. Verifique se o banco atual contém a conta criadora e se ela continuará acessível.
4. Execute os testes automatizados e faça testes manuais de login Google, cadastro, sessão, criador e beta.
5. Só então avalie o merge. Este documento e os testes não migram SQLite para Postgres/Blob e não garantem persistência entre instâncias.

**Importante:** esta mudança não deve ser implantada antes de configurar as variáveis. Ela não faz migração de dados nem apaga os bancos existentes.
