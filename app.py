from flask import Flask, render_template_string

app = Flask(__name__)

HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robô de Cobranças | COMBO UBS</title>
<style>
body {font-family:Arial;background:#f2f5fa;margin:0;color:#24344d}
header {background:#203657;color:white;padding:24px}
main {max-width:1100px;margin:30px auto;padding:15px}
.cards {display:grid;grid-template-columns:repeat(3,1fr);gap:15px}
.card {background:white;padding:22px;border-radius:12px}
section {background:white;padding:24px;margin-top:20px;border-radius:12px}
button {background:#2056a5;color:white;border:0;padding:12px 18px;border-radius:7px}
</style>
</head>
<body>
<header>
<h2>🤖 Robô de Cobranças — COMBO UBS / SISLOGA</h2>
<p>Controle de notas fiscais e mensagens pelo WhatsApp Business</p>
</header>
<main>
<div class="cards">
<div class="card"><h2>0</h2>Notas cadastradas</div>
<div class="card"><h2>0</h2>Contatos cadastrados</div>
<div class="card"><h2>0</h2>Mensagens preparadas</div>
</div>
<section>
<h2>Central de cobranças</h2>
<p>O painel está funcionando. Os dados serão carregados na próxima etapa.</p>
<p>Mensagens serão preparadas automaticamente, mas o envio dependerá do seu clique no WhatsApp.</p>
<button onclick="window.open('https://web.whatsapp.com/','_blank')">
Conectar ao WhatsApp Web
</button>
</section>
</main>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
