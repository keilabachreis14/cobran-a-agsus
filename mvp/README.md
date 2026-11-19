# Robô de Cobranças — COMBO UBS / SISLOGA (MVP)

Aplicação local Flask para importar contatos e notas por CSV, preparar mensagens e abrir a conversa no WhatsApp com o texto preenchido. O envio permanece manual.

## Executar
No Windows, execute `iniciar_windows.bat`. Em outros ambientes:

```bash
pip install -r requirements.txt
python app.py
```

Acesse `http://127.0.0.1:8765`.

## CSV de contatos
Colunas: `municipio,uf,servidor,telefone,telefone_validado,autorizado`. Telefone com DDI 55; os dois últimos campos aceitam 1 ou 0. Duplicatas são ignoradas.

## CSV de notas
Colunas: `nf,municipio,uf,transportadora,status,previsao_entrega,data_entrega,bloqueada`. Datas em AAAA-MM-DD; status: `faturada`, `em_transporte` ou `entregue`.

## Limitações conhecidas antes de uso operacional
- O MVP não consulta automaticamente a Agência Brasileira nem qualquer transportadora.
- O MVP não envia mensagens: apenas abre links wa.me para revisão e envio manual.
- A rotação dos quatro modelos é global por etapa, não individual por cliente.
- Não há autenticação de usuários, proteção CSRF, trilha de auditoria completa nem interface para marcar NF assinada.
- O banco SQLite é criado em `mvp/data/robo_cobrancas.sqlite3`, separado de qualquer banco existente no projeto.
- A importação atualiza dados de notas existentes (mantendo bloqueio já ativo); validar a política de atualização antes de importar dados reais.
- O segredo Flask deve ser definido por variável de ambiente `FLASK_SECRET_KEY` antes de qualquer uso além de teste local.

**Não importar dados reais nem usar em produção até concluir os controles de segurança, testes e validação do fluxo.** Não versionar banco, CSVs, telefones, NFs ou credenciais.
