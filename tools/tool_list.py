from tools.calc_tool import calculator
from tools.compare_tool import compare_packages
from tools.generator_tool import generate_quote_proposal
from tools.intl_route_tool import estimate_intl_route_optimization
from tools.isp_tool import quote_isp_packages
from tools.product_tool import query_products
from tools.rag_tool import retrieve_sales_context
from tools.rule_engine import classify_sales_scene
from tools.requirement_parser import parse_requirements
from tools.sd_wan_tool import quote_sd_wan_packages

ALL_TOOLS = [
    calculator,
    classify_sales_scene,
    compare_packages,
    generate_quote_proposal,
    estimate_intl_route_optimization,
    quote_isp_packages,
    quote_sd_wan_packages,
    parse_requirements,
    query_products,
    retrieve_sales_context
]
