# Cime dos Mundos 5.0

Versão 5.0 online, preparada para integração com Vercel e deploy automático pelo GitHub.


## Smart Vision — configurar a IA

O Cime 5.0 pode usar Gemini para leitura de imagens. Para configurar no Windows local:

1. Execute `CONFIGURAR_IA.bat` na pasta do Cime.
2. Digite a chave Gemini no campo oculto do PowerShell.
3. Reinicie o Cime para carregar as variáveis do arquivo `.env`.

No deploy da Vercel, configure `GEMINI_API_KEY` como variável **Sensitive/Secret** em Production e Preview. Nunca coloque a chave em HTML, JavaScript do navegador, commits, screenshots ou mensagens. A chave da API pode gerar custos de uso; configuração de cobrança deve ser feita com um responsável.

Modelo padrão para visão: `gemini-3.8-flash`. OpenAI é opcional; suas variáveis ficam em `.env.example`.
