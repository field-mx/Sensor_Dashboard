import importlib.util
import pytest

def test_critical_channel_regression_blocks_activation_even_if_mean_improves():
    assert importlib.util.find_spec('demo_server.validation'), 'Candidate validation is not implemented'
    from demo_server.validation import assess_metrics
    recipe={'overall_max_ratio':1.02,'critical_max_ratio':1.10,'critical_absolute_normalized_mae':{'critical':.002}}
    result=assess_metrics(['critical','other'],[.01,1.],[.05,.5],recipe,True)
    assert not result['eligible'] and not result['critical_passed'] and result['overall_passed']
    assert 'critical' in ' '.join(result['reasons'])

def test_fault_regression_and_nonfinite_metrics_cannot_pass():
    assert importlib.util.find_spec('demo_server.validation'), 'Candidate validation is not implemented'
    from demo_server.validation import assess_metrics
    recipe={'overall_max_ratio':1.02,'critical_max_ratio':1.10,'critical_absolute_normalized_mae':{'critical':.002}}
    assert not assess_metrics(['critical'],[1.],[.9],recipe,False)['eligible']
    with pytest.raises(ValueError):assess_metrics(['critical'],[1.],[float('nan')],recipe,True)
    assert assess_metrics(['critical'],[1.],[1.],recipe,True)['eligible']
