import numpy as np

# In a real military-grade system, this would load a pre-trained model:
# import joblib
# model = joblib.load('path/to/risk_model.pkl')

def calculate_ml_risk_score(expansion_rate, slope, dist_to_glacier, dist_to_fault):
    """
    Simulated Random Forest inference for GLOF risk.
    """
    # This is a placeholder for the real ML inference
    # input_features = np.array([[expansion_rate, slope, dist_to_glacier, dist_to_fault]])
    # score = model.predict_proba(input_features)[0][1]
    
    # Simple heuristic to simulate ML inference
    score = (expansion_rate * 0.4) + (slope * 0.3) + ((100 - dist_to_glacier) * 0.2) + ((100 - dist_to_fault) * 0.1)
    
    if score > 70:
        return "High"
    elif score > 40:
        return "Medium"
    else:
        return "Low"
