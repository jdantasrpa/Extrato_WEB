#!/usr/bin/env python3
"""
Monta um arquivo .eml (RFC 822) a partir de um assunto e um corpo HTML.

O .eml gerado abre nativamente no Outlook. De lá, o usuário pode usar
"Salvar Como > Formato de Mensagem do Outlook (.msg)" para obter o .msg
final, já que este ambiente não tem como gerar o binário .msg de forma
confiável.

Uso:
    python3 gerar_eml.py \
        --assunto "[Ganhos] - Automação | Nome do Projeto [Melhoria]" \
        --corpo-html corpo.html \
        --saida "/mnt/user-data/outputs/nome-do-arquivo.eml" \
        [--de "joao.dantas@alvocard.com.br"] \
        [--para ""]

Este script NÃO gera conteúdo — apenas monta o envelope MIME em cima do
HTML já escrito por Claude seguindo references/estrutura-template.md.
"""

import argparse
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path


def montar_eml(assunto: str, corpo_html_path: str, saida_path: str,
                remetente: str = "", destinatario: str = "") -> None:
    corpo_html = Path(corpo_html_path).read_text(encoding="utf-8")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = destinatario
    msg["Date"] = formatdate(localtime=True)

    # Versão texto simples como fallback (bem básica, o HTML é o principal)
    texto_simples = (
        "Este e-mail foi gerado em HTML. Abra em um cliente que suporte "
        "HTML para ver o conteúdo formatado."
    )
    msg.attach(MIMEText(texto_simples, "plain", "utf-8"))
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    saida = Path(saida_path)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_bytes(msg.as_bytes())
    print(f"Arquivo .eml gerado em: {saida}")


def main():
    parser = argparse.ArgumentParser(description="Gera um .eml a partir de assunto + corpo HTML")
    parser.add_argument("--assunto", required=True, help="Assunto do e-mail")
    parser.add_argument("--corpo-html", required=True, help="Caminho do arquivo HTML com o corpo do e-mail")
    parser.add_argument("--saida", required=True, help="Caminho de saída do .eml")
    parser.add_argument("--de", default="", help="E-mail do remetente (opcional, pode ficar em branco)")
    parser.add_argument("--para", default="", help="E-mail do destinatário (opcional, pode ficar em branco)")
    args = parser.parse_args()

    montar_eml(
        assunto=args.assunto,
        corpo_html_path=args.corpo_html,
        saida_path=args.saida,
        remetente=args.de,
        destinatario=args.para,
    )


if __name__ == "__main__":
    main()
