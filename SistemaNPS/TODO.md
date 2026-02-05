# TODO - Ajustes Finais Sistema NPS Ressalvas

## Backend - PDF Layout
- [ ] Corrigir posicionamento dinâmico da imagem: renderizar texto primeiro, posicionar imagem abaixo do último bloco de texto, evitar sobreposição e saída de página
- [ ] Padronizar captura inicial: preservar aspect ratio, centralizar na página usando algoritmo proporcional

## Backend - Robustez
- [ ] Garantir atomicidade lógica: mover update do processo após inserts bem-sucedidos, reverter status e URL se inserts falharem
- [ ] Consolidar validações: mover todas validações (CPF, representante, campos imagens, duplicatas, tamanho) antes de gerar PDF/upload/update

## Frontend - Unicidade ITEM
- [ ] Detectar duplicidade em tempo real: adicionar listener para inputs de item, destacar campos duplicados com borda vermelha
- [ ] Manter lógica validarFormulario() intacta

## Testes e Verificação
- [ ] Verificar PDF layout com descrições longas
- [ ] Testar rollback em falhas de insert
- [ ] Testar validações antecipadas
- [ ] Testar destaque de duplicatas no frontend
