# Sistema NPS - Fleximedical / Kure

O Sistema NPS é uma aplicação Full Stack desenvolvida com FastAPI, focada na gestão de feedback e conformidade para a Fleximedical/Kure. O sistema centraliza processos críticos de interação com o cliente, garantindo a coleta de métricas de satisfação e validações legais.

## Tecnologias e Hospedagem
- Framework: FastAPI (Python).

- Banco de Dados: Supabase (PostgreSQL).

- Hospedagem: Render.

- Frontend: HTML5 com Jinja2 Templates.

## Pilares do Sistema
O projeto está estruturado em três funcionalidades principais para atender às necessidades da empresa:

- NPS (Net Promoter Score): Coleta e análise do nível de satisfação dos clientes através de formulários dinâmicos.

- Termo de Aceite: Gestão e registro de concordância com termos de uso ou serviços.

- Ressalvas: Sistema de registro de observações e exceções durante o atendimento ou processo de feedback.

## Organização do Código
- app/routers/: Gerencia as rotas de cada módulo (nps.py, termo.py, ressalvas.py).

- app/services/: Contém a lógica de geração de documentos, incluindo relatórios em PDF.

- templates/: Interface visual para usuários e administradores.

- schemas.py: Definições de modelos de dados para integração com o Supabase.

## Instalação e Execução Local
Clone o repositório:

Bash
git clone https://github.com/HenriqueNunes-2008/SistemaNPS.git
cd SistemaNPS
Configure o Ambiente Virtual:

Bash
- python -m venv venv
- Ative o venv:
- Windows: .\venv\Scripts\activate | Linux: source venv/bin/activate
Instale as dependências:

Bash
- pip install -r requirements.txt
- Variáveis de Ambiente:
- Certifique-se de configurar as credenciais do Supabase (URL e API KEY) no seu ambiente ou arquivo .env.

Inicie o Servidor:

Bash
- uvicorn app.main:app --reload
