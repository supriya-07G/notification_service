# utils/translate_dict.py

EN_TO_PT = {
    "Hi": "Olá",
    "Hello": "Olá",
    "appointment": "agendamento",
    "reminder": "lembrete",
    "tomorrow": "amanhã",
    "today": "hoje",
    "at": "às",
    "with": "com",
    "for": "para",
    "Please reply YES to confirm": "Responda SIM para confirmar",
    "Please reply NO to cancel": "Responda NÃO para cancelar",
    "or call": "ou ligue para",
    "to reschedule": "para reagendar",
    "Thank you": "Obrigado",
    "Thanks": "Obrigado",
    "EcoSave Home Solutions": "EcoSave Home Solutions",
    "Your": "Seu",
    "is scheduled for": "está agendado para",
    "Service": "Serviço",
    "Installation": "Instalação",
    "Estimate": "Orçamento",
    "Inspection": "Inspeção",
    "We will arrive between": "Chegaremos entre",
    "and": "e",
    "If you have any questions": "Se você tiver alguma dúvida",
    "please contact us": "entre em contato conosco",
}

def translate_text(text: str) -> str:
    """Very simple string replacement based translator for templates."""
    if not text:
        return text
        
    result = text
    # Sort keys by length descending to prevent partial match replacements
    for eng in sorted(EN_TO_PT.keys(), key=len, reverse=True):
        pt = EN_TO_PT[eng]
        # Very crude replacement
        result = result.replace(eng, pt)
        # Also replace lowercase versions for common words
        if eng.islower():
            result = result.replace(eng.capitalize(), pt.capitalize())
        
    return result
