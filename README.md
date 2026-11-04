# Balancete em gráficos

Transforma o PDF de um balancete contábil em um relatório visual em HTML, pronto para o contador mostrar ao cliente.

Feito para um escritório de contabilidade cujo sistema contábil não tem API pública e só exporta o balancete em PDF.

## O que o relatório mostra

- DRE resumida, com o percentual de cada linha sobre a receita
- resultado em cascata (receita, custos, despesas e lucro)
- ranking das maiores despesas
- principais fornecedores

## Como funciona

1. **Extração.** O PDF é lido pela posição de cada palavra na página (PyMuPDF), não como texto corrido. É isso que mantém a conta, o recuo da hierarquia e os valores na mesma linha.
2. **Transformação.** As contas viram uma árvore pelo recuo da descrição. Os saldos são convertidos com sinal (D positivo, C negativo) e agrupados em receita, custo e despesa.
3. **Validação.** O lucro calculado é comparado com a conta "Lucro do Exercício" do próprio balancete. O relatório mostra se bateu ou um alerta com a diferença, para revisar a classificação das contas antes de apresentar.

## Uso

Arraste o PDF do balancete sobre o executável, ou abra o executável e escolha o arquivo. O relatório `<nome do PDF> - Relatório.html` é gerado na mesma pasta e abre no navegador.

Para rodar pelo código:

```
pip install pymupdf
python balancete_graficos.py caminho/do/balancete.pdf
```

Para gerar o executável único:

```
pip install pyinstaller
pyinstaller --onefile --noconsole --name Balancete-Graficos balancete_graficos.py
```

## Privacidade

Nenhum balancete ou relatório de cliente fica neste repositório. Todo o processamento acontece no computador de quem usa, sem enviar dados para a internet.
