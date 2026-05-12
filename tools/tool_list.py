from tools.calc_tool import calculator
from tools.intl_route_tool import estimate_intl_route_optimization
from tools.isp_tool import quote_isp_packages
from tools.product_tool import query_products
from tools.rule_engine import classify_sales_scene
from tools.requirement_parser import parse_requirements
from tools.sd_wan_tool import quote_sd_wan_packages

ALL_TOOLS = [
    calculator,
    classify_sales_scene,
    estimate_intl_route_optimization,
    quote_isp_packages,
    quote_sd_wan_packages,
    parse_requirements,
    query_products
]
