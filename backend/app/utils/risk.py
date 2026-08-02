def calculate_risk(area_ha: float) -> str:
    """
    Simple area-based GLOF risk assessment.
    Can be expanded later with volume, freeboard, slope, population etc.
    """
    if area_ha is None:
        return "Low"

    if area_ha >= 40:
        return "High"
    elif area_ha >= 15:
        return "Medium"
    else:
        return "Low"