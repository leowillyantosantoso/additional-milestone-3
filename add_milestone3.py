import os
import libcellml
import requests
import json
from rdflib import Graph

PMR_WORKSPACE_DIR = os.path.expanduser("~/Downloads/pmr/workspace")
BASELINE_UNITS_URL = "https://raw.githubusercontent.com/nickerso/cellml-to-fc/main/baseline_units.cellml"
RDF_OPB_URL = "https://raw.githubusercontent.com/nickerso/cellml-to-fc/main/rdf_unit_cellml.ttl"
RDF_OPB_LOCAL = "rdf_unit_cellml.ttl"

def download_file(url, local_path):
    if not os.path.exists(local_path):
        r = requests.get(url)
        r.raise_for_status()
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(r.text)

def find_cellml_files(root_dir):
    cellml_files = []
    for dirpath, dirs, files in os.walk(root_dir):
        for file in files:
            if file.endswith(".cellml"):
                cellml_files.append(os.path.join(dirpath, file))
    return cellml_files

def parse_baseline_units():
    download_file(BASELINE_UNITS_URL, "baseline_units.cellml")
    parser = libcellml.Parser()
    parser.setStrict(False)
    with open("baseline_units.cellml", "r", encoding="utf-8") as f:
        baseline_content = f.read()
    baseline_model = parser.parseModel(baseline_content)
    baseline_units = {}
    for i in range(baseline_model.unitsCount()):
        units = baseline_model.units(i)
        baseline_units[units.name()] = units
    return baseline_units

def load_opb_mappings(rdf_file_path=RDF_OPB_LOCAL):
    download_file(RDF_OPB_URL, rdf_file_path)
    opb_map = {}
    with open(rdf_file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("@prefix"):
                continue
            if "is_unit_of:" in line and "opb:OPB_" in line:
                # ex:um is_unit_of: opb:OPB_00269, opb:OPB_01064 .
                parts = line.split()
                if parts[0].startswith("ex:"):
                    unit_name = parts[0][3:]
                    opb_codes = []
                    # Find all opb:OPB_XXXX entries after 'is_unit_of:'
                    opb_part = line.split("is_unit_of:")[1]
                    for code in opb_part.split(","):
                        code = code.strip().replace("opb:OPB_", "OPB_").replace(".", "").replace(";", "")
                        if code.startswith("OPB_"):
                            opb_codes.append(code)
                    opb_map[unit_name] = opb_codes
    return opb_map

def resolve_imports(model, base_path):
    importer = libcellml.Importer()
    importer.resolveImports(model, base_path)
    return importer

def validate_model(model):
    validator = libcellml.Validator()
    validator.validateModel(model)
    return validator.errorCount() == 0

def get_unit_id(units):
    if hasattr(units, 'id') and units.id():
        return units.id()
    elif hasattr(units, 'cmetaId') and units.cmetaId():
        return units.cmetaId()
    else:
        return units.name()

def map_variable_units_to_opb(model, baseline_units, opb_map):
    mapped = 0
    total = 0
    mapping_details = []
    model_unit_names = [model.units(i).name() for i in range(model.unitsCount())]
    print(f"    Components: {model.componentCount()}")
    for i in range(model.componentCount()):
        comp = model.component(i)
        print(f"      Component '{comp.name()}': {comp.variableCount()} variables")
        for j in range(comp.variableCount()):
            var = comp.variable(j)
            unit_obj = var.units()
            # If unit_obj is a Units object, get its name
            if hasattr(unit_obj, "name"):
                unit_name = unit_obj.name()
            else:
                unit_name = unit_obj
            print(f"        Variable '{var.name()}' unit: '{unit_name}'")
            if unit_name in model_unit_names:
                units_obj = model.units(model_unit_names.index(unit_name))
            elif unit_name in baseline_units:
                units_obj = baseline_units[unit_name]
            else:
                continue
            total += 1
            for base_name, base_units in baseline_units.items():
                if libcellml.Units.compatible(units_obj, base_units):
                    opb_code = opb_map.get(base_name)
                    mapping_details.append({
                        "variable": var.name(),
                        "unit": unit_name,
                        "mapped_to": base_name,
                        "opb_code": opb_code
                    })
                    mapped += 1
                    break
    return mapped, total, mapping_details

def main():
    print("Scanning for CellML files...")
    cellml_files = find_cellml_files(PMR_WORKSPACE_DIR)
    print(f"Found {len(cellml_files)} CellML files.")

    print("Loading baseline units...")
    baseline_units = parse_baseline_units()

    print("Loading OPB mappings from RDF...")
    opb_map = load_opb_mappings()

    stats = []
    for idx, cellml_path in enumerate(cellml_files, 1):
        print(f"\n[{idx}/{len(cellml_files)}] Processing: {cellml_path}")
        parser = libcellml.Parser()
        parser.setStrict(False)
        with open(cellml_path, "r", encoding="utf-8") as f:
            content = f.read()
        model = parser.parseModel(content)
        if not model:
            print("  Failed to parse model.")
            continue

        # Resolve imports
        base_path = os.path.dirname(cellml_path)
        resolve_imports(model, base_path)

        # Validate model
        valid = validate_model(model)
        print(f"  Model valid: {valid}")

        # Map variable units to OPB
        mapped, total, mapping_details = map_variable_units_to_opb(model, baseline_units, opb_map)
        print(f"  Variables mapped: {mapped}/{total}")

        stats.append({
            "file": cellml_path,
            "variables_total": total,
            "variables_mapped": mapped,
            "mapping_details": mapping_details
        })

    # Save statistics
    with open("pmr_opb_mapping_stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)

    print("\nMapping statistics saved to pmr_opb_mapping_stats.json")

def generate_comprehensive_statistics(stats_json="pmr_opb_mapping_stats.json"):
    # Unit categories
    thermodynamic = {"K", "J", "mW", "S", "S_per_s"}
    quantities = {"um", "m2", "m3", "rad", "kg", "fmol", "kg_per_m2", "kg_per_m3", "mM", "mol_per_m2", "C_per_m2", "C_per_m3"}
    flow_rates = {"m_per_s", "m2_per_s", "m3_per_s", "rad_per_s", "kg_per_s", "fmol_per_s", "fA"}
    efforts = {"N", "J_per_m2", "Pa", "J_per_mol", "mV", "mM_per_s", "mol_per_m2_s", "C_per_m2_s", "C_per_m3_s"} 

    with open(stats_json, "r", encoding="utf-8") as f:
        stats = json.load(f)

    total_files = len(stats)
    total_vars = 0
    mapped_vars = 0
    unmapped_vars = 0

    mapped_units = []
    unmapped_units = []
    opb_codes = []

    category_counts = {"Quantities": 0, "Flow rates": 0, "Efforts": 0, "Thermodynamics": 0}

    for file_stat in stats:
        total_vars += file_stat.get("variables_total", 0)
        mapped_vars += file_stat.get("variables_mapped", 0)
        for detail in file_stat.get("mapping_details", []):
            unit = detail.get("mapped_to")
            opb_list = detail.get("opb_code")
            mapped_units.append(unit)
            # Handle OPB codes (can be list or single value)
            if isinstance(opb_list, list):
                if opb_list:
                    for opb in opb_list:
                        opb_codes.append(opb)
                else:
                    unmapped_units.append(unit)
            elif opb_list:
                opb_codes.append(opb_list)
            else:
                unmapped_units.append(unit)
            # Categorize mapped units
            if opb_list:  # Only categorize if mapped to OPB
                if unit in thermodynamic:
                    category_counts["Thermodynamics"] += 1
                elif unit in quantities:
                    category_counts["Quantities"] += 1
                elif unit in flow_rates:
                    category_counts["Flow rates"] += 1
                elif unit in efforts:
                    category_counts["Efforts"] += 1
        # Add unmapped units from unmapped_details if present
        if "unmapped_details" in file_stat:
            for detail in file_stat["unmapped_details"]:
                unmapped_units.append(detail.get("unit"))
        unmapped_vars += file_stat.get("variables_total", 0) - file_stat.get("variables_mapped", 0)

    # Top 10 mapped units
    from collections import Counter
    mapped_counter = Counter(mapped_units)
    unmapped_counter = Counter(unmapped_units)
    opb_counter = Counter(opb_codes)

    print("-----------")
    print("OVERVIEW")
    print("-----------")
    print(f"Total number of files processed: {total_files}")
    print(f"Total number of variables processed: {total_vars}")
    print(f"Number of variables successfully mapped: {mapped_vars}")
    print(f"Number of variables not mapped: {unmapped_vars}")

    print("\n--------------------------")
    print("CATEGORY BREAKDOWN")
    print("--------------------------")
    for cat, count in category_counts.items():
        percent = (count / mapped_vars * 100) if mapped_vars else 0
        print(f"{cat}: {count} ({percent:.1f}%)")

    print("\n------------------------")
    print("TOP 10 MAPPED UNITS")
    print("------------------------")
    for unit, count in mapped_counter.most_common(10):
        print(f"{unit}: {count}")

    print("\n---------------------------")
    print("TOP 10 UNMAPPED UNITS")
    print("---------------------------")
    for unit, count in unmapped_counter.most_common(10):
        print(f"{unit}: {count}")

    OPB_DESCRIPTIONS = {
        "OPB_01532": "Volumetric concentration of particles",
         "OPB_00340": "Concentration of chemical",
         "OPB_00378": "Chemical potential",
         "OPB_00509": "Fluid pressure",
         "OPB_01238": "Charge areal density",
         "OPB_01237": "Charge volumetric density",
         "OPB_00562": "Energy amount",
         "OPB_01053": "Mechanical stress",
         "OPB_00293": "Temperature",
         "OPB_00034": "Mechanical force",
         "OPB_00100": "Thermodynamic entropy amount",
         "OPB_00564": "Entropy flow rate",
         "OPB_00411": "Charge amount",
         "OPB_00592": "Chemical amount flow rate",
         "OPB_00544": "Particle flow rate",
         "OPB_01226": "Mass of solid entity",
         "OPB_01593": "Areal density of mass",
         "OPB_01619": "Volumnal density of matter",
         "OPB_01220": "Material flow rate",
         "OPB_00295": "Spatial area",
         "OPB_01643": "Tensile distortion velocity",
         "OPB_00523": "Spatial volume",
         "OPB_00299": "Fluid flow rate",
         "OPB_01058": "Membrane potential",
         "OPB_01169": "Electrodiffusional potential",
         "OPB_00563": "Energy flow rate",
         "OPB_00251": "Lineal translational velocity",
         "OPB_01529": "Areal concentration of chemical",
         "OPB_01530": "Areal concentration of particles",
         "OPB_01601": "Rotational displacement",
         "OPB_01064": "Spatial span",
         "OPB_01490": "Rotational solid velocity",
         "OPB_00402": "Temporal location",
         "OPB_00506": "Electrical potential",
         "OPB_00154": "Fluid volume",
         "OPB_01072": "Plane angle",
         "OPB_00318": "Charge flow rate",
         "OPB_00425": "Molar amount of chemical",
         "OPB_00269": "Translational displacement",
         "OPB_01376": "Tensile distortion",

    }

    print("\n-----------------------")
    print("TOP 10 OPB MAPPING")
    print("-----------------------")
    for opb, count in opb_counter.most_common(10):
        opb_clean = opb.strip(" ,;.")
        desc = OPB_DESCRIPTIONS.get(opb_clean, "")
        if desc:
            print(f"{opb}: {count} ({desc})")
        else:
            print(f"{opb}: {count}")

if __name__ == "__main__":
    main()
    generate_comprehensive_statistics()


# Last version, working on pmr_cellml_models folder
# import os
# import libcellml
# import requests
# from collections import defaultdict
# import statistics
# import json
# from rdflib import Graph, Namespace, RDF, RDFS


# ALLOWED_BASE_UNITS = {
#     'second', 'radian', 'kilogram', 'gram', 'joule', 'meter', 'metre', 'pascal', 
#     'newton', 'tesla', 'ampere', 'weber', 'candela', 'celsius', 'lux', 'becquerel', 
#     'coulomb', 'sievert', 'watt', 'hertz', 'steradian', 'volt', 'farad', 'siemens', 
#     'henry', 'lumen', 'gray', 'mole', 'katal', 'kelvin', 'litre', 'ohm', 'dimensionless'
# }

# def download_baseline_units():
#     """Download the baseline_units.cellml file from GitHub"""
#     url = "https://raw.githubusercontent.com/nickerso/cellml-to-fc/main/baseline_units.cellml"
#     try:
#         response = requests.get(url)
#         response.raise_for_status()
#         return response.text
#     except requests.RequestException as e:
#         print(f"Error downloading baseline_units: {e}")
#         return None

# def parse_baseline_units():
#     """Parse the baseline_units.cellml file and return a dictionary of units"""
#     baseline_content = download_baseline_units()
#     if not baseline_content:
#         raise Exception("Failed to download baseline_units.cellml from GitHub")
    
#     parser = libcellml.Parser()
#     parser.setStrict(False)
#     baseline_model = parser.parseModel(baseline_content)
    
#     if not baseline_model:
#         raise Exception("Failed to parse baseline_units.cellml")
    
#     baseline_units = {}
#     for i in range(baseline_model.unitsCount()):
#         units = baseline_model.units(i)
#         baseline_units[units.name()] = units
    
#     print(f"Parsed {len(baseline_units)} units from baseline_units.cellml")
#     return baseline_units

# def load_opb_mappings_with_cmeta_ids(rdf_file_path="rdf_unit_cellml.ttl"):
#     """Load OPB mappings by matching cmeta IDs between CellML units and RDF file"""
#     opb_mappings = {}
    
#     if not os.path.exists(rdf_file_path):
#         raise Exception(f"RDF file {rdf_file_path} not found")
    
#     try:
#         # Load RDF file using rdflib
#         g = Graph()
#         g.parse(rdf_file_path, format="turtle")
        
#         # The actual predicate is http://semanticscience.org/resource/SIO_000222
#         sio_predicate = "http://semanticscience.org/resource/SIO_000222"
#         print(f"Using SIO predicate: {sio_predicate}")
        
#         # Query for all unit to OPB mappings using the SIO predicate
#         opb_count = 0
#         for s, p, o in g.triples((None, None, None)):
#             # Check if this triple uses our target predicate and points to an OPB code
#             predicate_str = str(p)
#             object_str = str(o)
            
#             if ("SIO_000222" in predicate_str or sio_predicate in predicate_str) and "OPB_" in object_str:
                
#                 # Get the subject (unit) and object (OPB)
#                 unit_uri = str(s)
#                 opb_uri = object_str
                
#                 # Extract identifier from unit URI
#                 if "#" in unit_uri:
#                     unit_id = unit_uri.split("#")[-1]
#                 elif "/" in unit_uri:
#                     unit_id = unit_uri.split("/")[-1]
#                 else:
#                     unit_id = unit_uri
                
#                 # Clean up unit ID
#                 unit_id = unit_id.replace('"', '').replace("'", "").strip()
                
#                 # Extract OPB code
#                 if "OPB_" in opb_uri:
#                     opb_code = opb_uri.split("OPB_")[-1]
#                     opb_code = opb_code.split(">")[0] if ">" in opb_code else opb_code
#                     opb_code = opb_code.split("/")[-1]  # Remove any URL parts
#                     opb_code = f"OPB_{opb_code}"
                    
#                     opb_mappings[unit_id] = opb_code
#                     opb_count += 1
#                     print(f"  Mapping: {unit_id} → {opb_code}")
        
#         print(f"Loaded {opb_count} OPB mappings using SIO predicate")
        
#         # If no mappings found, try a more direct approach
#         if opb_count == 0:
#             print("Trying alternative parsing approach...")
#             for s, p, o in g:
#                 if "OPB_" in str(o):
#                     # Check if this looks like a unit→OPB mapping
#                     unit_uri = str(s)
#                     opb_uri = str(o)
                    
#                     # Extract identifiers
#                     unit_id = unit_uri.split("#")[-1] if "#" in unit_uri else unit_uri.split("/")[-1]
#                     opb_code = opb_uri.split("OPB_")[-1].split(">")[0]
#                     opb_code = f"OPB_{opb_code}"
                    
#                     opb_mappings[unit_id] = opb_code
#                     opb_count += 1
#                     print(f"  Alt Mapping: {unit_id} → {opb_code}")
        
#         return opb_mappings
        
#     except Exception as e:
#         print(f"Error parsing RDF file with rdflib: {e}")
#         import traceback
#         traceback.print_exc()
#         raise

# def get_unit_cmeta_id(units):
#     """Get the cmeta ID from a units object"""
#     try:
#         # Try various methods to get identifier
#         if hasattr(units, 'id') and units.id():
#             return units.id()
#         elif hasattr(units, 'cmetaId') and units.cmetaId():
#             return units.cmetaId()
#         else:
#             # Use unit name as fallback identifier
#             return units.name()
#     except:
#         return units.name()

# def analyze_model_with_cmeta_matching(file_path, baseline_units, opb_mappings):
#     """Analyze a CellML model using cmeta ID matching"""
#     results = {
#         'filename': os.path.basename(file_path),
#         'total_units': 0,
#         'mapped_units': 0,
#         'unmapped_units': 0,
#         'mapped_details': [],
#         'unmapped_details': [],
#     }
    
#     try:
#         with open(file_path, 'r', encoding='utf-8') as file:
#             target_content = file.read()
        
#         parser = libcellml.Parser()
#         parser.setStrict(False)
#         target_model = parser.parseModel(target_content)
        
#         if not target_model:
#             return results
        
#         results['total_units'] = target_model.unitsCount()
        
#         # Process each unit in the model
#         for i in range(target_model.unitsCount()):
#             target_unit = target_model.units(i)
#             target_unit_name = target_unit.name()
#             target_id = get_unit_cmeta_id(target_unit)
            
#             # First try direct compatibility with baseline units
#             mapped = False
#             for baseline_name, baseline_unit in baseline_units.items():
#                 if libcellml.Units.compatible(target_unit, baseline_unit):
#                     mapped = True
#                     baseline_id = get_unit_cmeta_id(baseline_unit)
                    
#                     # Check if we have OPB mapping for this baseline unit
#                     if baseline_id in opb_mappings:
#                         results['mapped_units'] += 1
#                         results['mapped_details'].append({
#                             'target_unit': target_unit_name,
#                             'target_id': target_id,
#                             'baseline_unit': baseline_name,
#                             'baseline_id': baseline_id,
#                             'opb_mapping': opb_mappings[baseline_id],
#                         })
#                     else:
#                         results['unmapped_units'] += 1
#                         results['unmapped_details'].append({
#                             'target_unit': target_unit_name,
#                             'target_id': target_id,
#                             'reason': f'No OPB mapping for baseline unit: {baseline_name}',
#                         })
#                     break
            
#             # If not compatible with baseline, check if target unit has direct OPB mapping
#             if not mapped and target_id in opb_mappings:
#                 results['mapped_units'] += 1
#                 results['mapped_details'].append({
#                     'target_unit': target_unit_name,
#                     'target_id': target_id,
#                     'baseline_unit': 'direct',
#                     'baseline_id': target_id,
#                     'opb_mapping': opb_mappings[target_id],
#                 })
#                 mapped = True
            
#             if not mapped:
#                 results['unmapped_units'] += 1
#                 results['unmapped_details'].append({
#                     'target_unit': target_unit_name,
#                     'target_id': target_id,
#                     'reason': 'No compatible baseline unit found and no direct OPB mapping',
#                 })
        
#     except Exception as e:
#         results['error'] = str(e)
#         import traceback
#         traceback.print_exc()
    
#     return results

# def analyze_all_models(folder_path):
#     """Analyze all CellML models using cmeta ID matching"""
#     if not os.path.exists(folder_path):
#         print(f"Error: Folder '{folder_path}' not found!")
#         return None
    
#     print(f"Analyzing all CellML models in: {folder_path}")
#     print("=" * 60)
    
#     # Load baseline units and OPB mappings
#     try:
#         baseline_units = parse_baseline_units()
#         opb_mappings = load_opb_mappings_with_cmeta_ids()
#     except Exception as e:
#         print(f"Failed to load baseline units or OPB mappings: {e}")
#         return None
    
#     # Get all CellML files
#     cellml_files = []
#     for root_dir, dirs, files in os.walk(folder_path):
#         for file in files:
#             if file.endswith('.cellml') or file.endswith('.xml'):
#                 cellml_files.append(os.path.join(root_dir, file))
    
#     print(f"Found {len(cellml_files)} CellML files")
    
#     # Statistics collection
#     stats = {
#         'total_files': 0,
#         'successful_parses': 0,
#         'total_units': 0,
#         'mapped_units': 0,
#         'unmapped_units': 0,
#         'most_common_mapped_units': defaultdict(int),
#         'most_common_unmapped_units': defaultdict(int),
#         'opb_coverage': defaultdict(int),
#         'file_stats': []
#     }
    
#     # Process each file
#     for i, file_path in enumerate(cellml_files, 1):
#         if i % 100 == 0:
#             print(f"Processing file {i}/{len(cellml_files)}")
        
#         file_stats = analyze_model_with_cmeta_matching(file_path, baseline_units, opb_mappings)
        
#         if 'error' not in file_stats:
#             stats['total_files'] += 1
#             stats['successful_parses'] += 1
#             stats['total_units'] += file_stats['total_units']
#             stats['mapped_units'] += file_stats['mapped_units']
#             stats['unmapped_units'] += file_stats['unmapped_units']
            
#             # Track statistics - convert tuple keys to strings for JSON compatibility
#             for detail in file_stats['mapped_details']:
#                 # Use string key instead of tuple
#                 key = f"{detail['target_unit']}→{detail['baseline_unit']}"
#                 stats['most_common_mapped_units'][key] += 1
#                 stats['opb_coverage'][detail['opb_mapping']] += 1
            
#             for detail in file_stats['unmapped_details']:
#                 stats['most_common_unmapped_units'][detail['target_unit']] += 1
            
#             stats['file_stats'].append(file_stats)
    
#     # Calculate final statistics
#     if stats['total_units'] > 0:
#         stats['mapping_success_rate'] = (stats['mapped_units'] / stats['total_units']) * 100
    
#     return stats

# def generate_statistical_report(stats, output_file="unit_mapping_statistics.json"):
#     """Generate comprehensive statistical report"""
#     # Convert defaultdicts to regular dicts for JSON serialization
#     report = {
#         'overview': {
#             'total_files_processed': stats['total_files'],
#             'total_units_analyzed': stats['total_units'],
#             'mapped_units': stats['mapped_units'],
#             'unmapped_units': stats['unmapped_units'],
#             'mapping_success_rate': stats.get('mapping_success_rate', 0)
#         },
#         'most_common_mappings': [
#             {'mapping': k, 'count': v}
#             for k, v in sorted(stats['most_common_mapped_units'].items(), 
#                              key=lambda x: x[1], reverse=True)[:20]
#         ],
#         'most_common_unmapped_units': [
#             {'unit': k, 'count': v}
#             for k, v in sorted(stats['most_common_unmapped_units'].items(), 
#                              key=lambda x: x[1], reverse=True)[:20]
#         ],
#         'opb_coverage': [
#             {'opb_code': k, 'count': v}
#             for k, v in sorted(stats['opb_coverage'].items(), 
#                              key=lambda x: x[1], reverse=True)
#         ]
#     }
    
#     # Save JSON report
#     with open(output_file, 'w', encoding='utf-8') as f:
#         json.dump(report, f, indent=2, ensure_ascii=False)
    
#     return report

# def main():
#     # Analyze all CellML models in the folder
#     folder_path = "pmr_cellml_models"
    
#     if not os.path.exists(folder_path):
#         print(f"Error: Folder '{folder_path}' not found!")
#         return
    
#     if not os.path.exists("rdf_unit_cellml.ttl"):
#         print("Error: RDF file 'rdf_unit_cellml.ttl' not found!")
#         return
    
#     print("=" * 80)
#     print("CELLML UNIT MAPPING ANALYSIS WITH CMETA ID MATCHING")
#     print("=" * 80)
    
#     # Perform analysis
#     stats = analyze_all_models(folder_path)
    
#     if stats and stats['total_files'] > 0:
#         # Generate statistical report
#         report = generate_statistical_report(stats)
        
#         print(f"\n{'='*60}")
#         print("ANALYSIS RESULTS")
#         print(f"{'='*60}")
#         print(f"Total files processed: {report['overview']['total_files_processed']}")
#         print(f"Total units analyzed: {report['overview']['total_units_analyzed']}")
#         print(f"Mapped units: {report['overview']['mapped_units']}")
#         print(f"Unmapped units: {report['overview']['unmapped_units']}")
#         print(f"Mapping success rate: {report['overview']['mapping_success_rate']:.1f}%")
        
#         print(f"\nTop 5 most common mappings:")
#         for i, mapping in enumerate(report['most_common_mappings'][:5], 1):
#             print(f"  {i}. {mapping['mapping']} ({mapping['count']} times)")
        
#         print(f"\nTop 5 most common unmapped units:")
#         for i, unit in enumerate(report['most_common_unmapped_units'][:5], 1):
#             print(f"  {i}. {unit['unit']} ({unit['count']} times)")
        
#         print(f"\nTop 5 most used OPB codes:")
#         for i, opb in enumerate(report['opb_coverage'][:5], 1):
#             print(f"  {i}. {opb['opb_code']} ({opb['count']} times)")
        
#         print(f"\nDetailed report saved to: unit_mapping_statistics.json")
#         print(f"Analysis completed successfully!")
#     else:
#         print("Analysis failed or no files were processed.")

# if __name__ == "__main__":
#     main()

# import os
# import libcellml
# import requests
# import re
# from collections import defaultdict

# # List of allowed fundamental base units
# ALLOWED_BASE_UNITS = {
#     'second', 'radian', 'kilogram', 'gram', 'joule', 'meter', 'metre', 'pascal', 
#     'newton', 'tesla', 'ampere', 'weber', 'candela', 'celsius', 'lux', 'becquerel', 
#     'coulomb', 'sievert', 'watt', 'hertz', 'steradian', 'volt', 'farad', 'siemens', 
#     'henry', 'lumen', 'gray', 'mole', 'katal', 'kelvin'
# }

# def download_baseline_units():
#     """Download the baseline_units.cellml file from GitHub"""
#     url = "https://raw.githubusercontent.com/nickerso/cellml-to-fc/main/baseline_units.cellml"
#     try:
#         response = requests.get(url)
#         response.raise_for_status()
#         return response.text
#     except requests.RequestException as e:
#         print(f"Error downloading baseline_units: {e}")
#         return None

# def parse_baseline_units():
#     """Parse the baseline_units.cellml file and return a dictionary of units"""
#     baseline_content = download_baseline_units()
#     if not baseline_content:
#         raise Exception("Failed to download baseline_units.cellml from GitHub")
    
#     parser = libcellml.Parser()
#     parser.setStrict(False)
#     baseline_model = parser.parseModel(baseline_content)
    
#     if not baseline_model:
#         raise Exception("Failed to parse baseline_units.cellml")
    
#     baseline_units = {}
#     for i in range(baseline_model.unitsCount()):
#         units = baseline_model.units(i)
#         baseline_units[units.name()] = units
    
#     print(f"Parsed {len(baseline_units)} units from baseline_units.cellml")
#     return baseline_units

# def load_opb_mappings(rdf_file_path="rdf_unit_cellml.ttl"):
#     """
#     Load OPB ontology mappings from local RDF/Turtle file
#     Handle multiple OPB mappings per unit
#     """
#     opb_mappings = {}
    
#     if not os.path.exists(rdf_file_path):
#         raise Exception(f"RDF file {rdf_file_path} not found")
    
#     try:
#         with open(rdf_file_path, 'r', encoding='utf-8') as f:
#             content = f.read()
        
#         print(f"Parsing RDF file: {rdf_file_path}")
        
#         # Parse the RDF content - specific to your format
#         lines = content.split('\n')
#         current_unit = None
        
#         for line in lines:
#             line = line.strip()
            
#             # Skip empty lines and prefix declarations
#             if not line or line.startswith('@prefix'):
#                 continue
            
#             # Look for lines with is_unit_of:
#             if 'is_unit_of:' in line:
#                 # Format: ex:C_per_m2 is_unit_of: opb:OPB_01238 .
#                 parts = [p for p in line.split() if p]  # Remove empty parts
                
#                 if len(parts) >= 3:
#                     # Extract unit name from ex: prefix (remove "ex:")
#                     unit_part = parts[0]
#                     if ':' in unit_part:
#                         unit_name = unit_part.split(':')[1]
#                     else:
#                         unit_name = unit_part
                    
#                     current_unit = unit_name
#                     if unit_name not in opb_mappings:
#                         opb_mappings[unit_name] = []
                    
#                     # Extract OPB codes from the rest of the line
#                     for part in parts[2:]:
#                         if 'opb:OPB_' in part:
#                             opb_code = part.replace('opb:OPB_', 'OPB_').rstrip('.,;')
#                             opb_mappings[unit_name].append(opb_code)
#                             print(f"  Loaded mapping: {unit_name} → {opb_code}")
            
#             # Handle multi-line OPB mappings (lines that continue with more OPB codes)
#             elif current_unit and 'opb:OPB_' in line:
#                 # Continuation line with more OPB codes
#                 for part in line.split():
#                     if 'opb:OPB_' in part:
#                         opb_code = part.replace('opb:OPB_', 'OPB_').rstrip('.,;')
#                         opb_mappings[current_unit].append(opb_code)
#                         print(f"  Loaded mapping: {current_unit} → {opb_code}")
        
#         print(f"Successfully loaded {sum(len(v) for v in opb_mappings.values())} OPB mappings for {len(opb_mappings)} units")
#         return opb_mappings
        
#     except Exception as e:
#         print(f"Error parsing RDF file: {e}")
#         import traceback
#         traceback.print_exc()
#         raise

# def are_units_compatible(unit1, unit2):
#     """
#     Check if two units are compatible, but be more strict about dimensional analysis
#     """
#     try:
#         # First check basic compatibility
#         if not libcellml.Units.compatible(unit1, unit2):
#             return False
        
#         # Additional checks to prevent false positives
#         # For example, rad_per_s (radian/second) should not be compatible with per_millisecond (1/second)
        
#         # Get the dimensional composition of both units
#         dim1 = get_unit_dimensions(unit1)
#         dim2 = get_unit_dimensions(unit2)
        
#         # Units are only compatible if they have exactly the same dimensions
#         return dim1 == dim2
        
#     except Exception as e:
#         print(f"Error in compatibility check: {e}")
#         return False

# def get_unit_dimensions(units):
#     """
#     Get a dimensional signature for a unit to ensure proper compatibility checking
#     Returns a tuple representing the dimensional composition
#     """
#     dimensions = []
#     for i in range(units.unitCount()):
#         try:
#             ref = units.unitAttributeReference(i)
#             exponent = units.unitAttributeExponent(i)
#             dimensions.append((ref, exponent))
#         except:
#             continue
    
#     # Sort for consistent comparison
#     return tuple(sorted(dimensions))

# def map_units_to_opb(target_file_path):
#     """Map units from target CellML file to OPB ontology using baseline units"""
#     print(f"Mapping units for: {os.path.basename(target_file_path)}")
#     print("=" * 60)
    
#     try:
#         # Load baseline units and OPB mappings
#         baseline_units = parse_baseline_units()
#         opb_mappings = load_opb_mappings()
        
#         # Parse target model
#         with open(target_file_path, 'r', encoding='utf-8') as file:
#             target_content = file.read()
        
#         parser = libcellml.Parser()
#         parser.setStrict(False)
#         target_model = parser.parseModel(target_content)
        
#         if not target_model:
#             print("Failed to parse target model")
#             return
        
#         print(f"Target model: {target_model.name()}")
#         print(f"Units in target model: {target_model.unitsCount()}")
        
#         # Map each unit in target model to OPB via baseline units
#         unit_mappings = []
        
#         for i in range(target_model.unitsCount()):
#             target_unit = target_model.units(i)
#             target_unit_name = target_unit.name()
            
#             print(f"\nProcessing target unit: {target_unit_name}")
            
#             # Find compatible baseline units with strict checking
#             compatible_baseline_units = []
#             for baseline_name, baseline_unit in baseline_units.items():
#                 print(f"  🔄 Checking against baseline: {baseline_name}")
                
#                 # Use recursive compatibility checking
#                 if are_units_compatible(target_unit, baseline_unit):
#                     mapped = True
#                     baseline_id = get_unit_cmeta_id(baseline_unit)
                    
#                     # Check if we have OPB mapping for this baseline unit
#                     if baseline_id in opb_mappings:
#                         compatible_baseline_units.append(baseline_name)
#                         print(f"  ✓ Compatible with baseline unit: {baseline_name}")
#                     else:
#                         print(f"  ⚠ No OPB mapping found for baseline unit: {baseline_name}")
#                 else:
#                     print(f"  ❌ NOT compatible with {baseline_name}")
            
#             # Map to OPB ontology (handle multiple mappings)
#             opb_terms = []
#             for baseline_name in compatible_baseline_units:
#                 if baseline_name in opb_mappings:
#                     for opb_term in opb_mappings[baseline_name]:
#                         opb_terms.append((baseline_name, opb_term))
#                         print(f"    → Maps to OPB: {opb_term}")
#                 else:
#                     print(f"    ⚠ No OPB mapping found for baseline unit: {baseline_name}")
            
#             unit_mappings.append({
#                 'target_unit': target_unit_name,
#                 'compatible_baseline_units': compatible_baseline_units,
#                 'opb_mappings': opb_terms
#             })
        
#         # Generate results
#         print("\n" + "=" * 60)
#         print("MAPPING RESULTS")
#         print("=" * 60)
        
#         save_mapping_results(unit_mappings, target_file_path, opb_mappings)
        
#         # Print summary
#         total_mapped = sum(1 for mapping in unit_mappings if mapping['opb_mappings'])
#         total_opb_terms = sum(len(mapping['opb_mappings']) for mapping in unit_mappings)
        
#         print(f"\nSummary for {os.path.basename(target_file_path)}:")
#         print(f"  Total units: {len(unit_mappings)}")
#         print(f"  Units with OPB mappings: {total_mapped}")
#         print(f"  Total OPB mappings: {total_opb_terms}")
#         print(f"  Units without OPB mappings: {len(unit_mappings) - total_mapped}")
        
#         return unit_mappings
        
#     except Exception as e:
#         print(f"Error in mapping process: {e}")
#         import traceback
#         traceback.print_exc()

# def save_mapping_results(unit_mappings, target_file_path, opb_mappings):
#     """Save mapping results to a file"""
#     filename = os.path.basename(target_file_path)
#     output_file = f"opb_mappings_{filename}.txt"
    
#     with open(output_file, 'w', encoding='utf-8') as f:
#         f.write(f"OPB ONTOLOGY MAPPINGS FOR: {filename}\n")
#         f.write("=" * 80 + "\n\n")
        
#         f.write("BASELINE UNITS TO OPB MAPPINGS REFERENCE:\n")
#         f.write("-" * 50 + "\n")
#         for unit_name, opb_codes in sorted(opb_mappings.items()):
#             f.write(f"{unit_name} → {', '.join([f'OPB.{code}' for code in opb_codes])}\n")
#         f.write("\n")
        
#         f.write("UNIT MAPPINGS:\n")
#         f.write("-" * 40 + "\n")
        
#         for mapping in unit_mappings:
#             f.write(f"\nTarget unit: {mapping['target_unit']}\n")
            
#             if mapping['compatible_baseline_units']:
#                 f.write("Compatible with baseline units: " + ", ".join(mapping['compatible_baseline_units']) + "\n")
#             else:
#                 f.write("No compatible baseline units found\n")
            
#             if mapping['opb_mappings']:
#                 f.write("OPB ontology mappings:\n")
#                 for baseline_name, opb_term in mapping['opb_mappings']:
#                     f.write(f"  {baseline_name} → OPB.{opb_term}\n")
#             else:
#                 f.write("No OPB ontology mapping found\n")
        
#         # Summary statistics
#         total_units = len(unit_mappings)
#         mapped_units = sum(1 for m in unit_mappings if m['opb_mappings'])
#         total_opb_terms = sum(len(m['opb_mappings']) for m in unit_mappings)
        
#         f.write(f"\n\nSUMMARY:\n")
#         f.write("-" * 40 + "\n")
#         f.write(f"Total units: {total_units}\n")
#         f.write(f"Units with OPB mappings: {mapped_units}\n")
#         f.write(f"Total OPB mappings: {total_opb_terms}\n")
#         f.write(f"Units without OPB mappings: {total_units - mapped_units}\n")
#         if total_units > 0:
#             f.write(f"Mapping success rate: {(mapped_units/total_units)*100:.1f}%\n")
    
#     print(f"Mapping results saved to: {output_file}")

# def main():
#     # Start with specific file for testing
#     target_file = "houart_1999.cellml"
    
#     if not os.path.exists(target_file):
#         print(f"Error: Target file '{target_file}' not found!")
#         return
    
#     if not os.path.exists("rdf_unit_cellml.ttl"):
#         print("Error: RDF file 'rdf_unit_cellml.ttl' not found!")
#         return
    
#     print("=" * 80)
#     print("TESTING RECURSIVE UNIT RESOLUTION")
#     print("=" * 80)
    
#     # Load baseline units and OPB mappings
#     try:
#         baseline_units = parse_baseline_units()
#         opb_mappings = load_opb_mappings()
#     except Exception as e:
#         print(f"Failed to load baseline units or OPB mappings: {e}")
#         return
    
#     # Test the specific file
#     results = analyze_single_model(target_file, baseline_units, opb_mappings)
    
#     print(f"\n" + "=" * 60)
#     print("FINAL RESULTS")
#     print("=" * 60)
#     print(f"Total units: {results['total_units']}")
#     print(f"Mapped units: {results['mapped_units']}")
#     print(f"Unmapped units: {results['unmapped_units']}")
    
#     if results['mapped_details']:
#         print(f"\nMAPPED UNITS:")
#         for detail in results['mapped_details']:
#             print(f"  {detail['target_unit']} → {detail['baseline_unit']} → OPB.{', '.join(detail['opb_mappings'])}")
    
#     if results['unmapped_details']:
#         print(f"\nUNMAPPED UNITS:")
#         for detail in results['unmapped_details']:
#             print(f"  {detail['target_unit']}: {detail['reason']}")

# if __name__ == "__main__":
#     main()

# import os
# import libcellml
# import requests
# import re
# from collections import defaultdict

# # List of allowed fundamental base units
# ALLOWED_BASE_UNITS = {
#     'second', 'radian', 'kilogram', 'gram', 'joule', 'meter', 'metre', 'pascal', 
#     'newton', 'tesla', 'ampere', 'weber', 'candela', 'celsius', 'lux', 'becquerel', 
#     'coulomb', 'sievert', 'watt', 'hertz', 'steradian', 'volt', 'farad', 'siemens', 
#     'henry', 'lumen', 'gray', 'mole', 'katal', 'kelvin'
# }

# def download_baseline_units():
#     """Download the baseline_units.cellml file from GitHub"""
#     url = "https://raw.githubusercontent.com/nickerso/cellml-to-fc/main/baseline_units.cellml"
#     try:
#         response = requests.get(url)
#         response.raise_for_status()
#         return response.text
#     except requests.RequestException as e:
#         print(f"Error downloading baseline_units: {e}")
#         return None

# def parse_baseline_units():
#     """Parse the baseline_units.cellml file and return a dictionary of units"""
#     baseline_content = download_baseline_units()
#     if not baseline_content:
#         raise Exception("Failed to download baseline_units.cellml from GitHub")
    
#     parser = libcellml.Parser()
#     parser.setStrict(False)
#     baseline_model = parser.parseModel(baseline_content)
    
#     if not baseline_model:
#         raise Exception("Failed to parse baseline_units.cellml")
    
#     baseline_units = {}
#     for i in range(baseline_model.unitsCount()):
#         units = baseline_model.units(i)
#         baseline_units[units.name()] = units
    
#     print(f"Parsed {len(baseline_units)} units from baseline_units.cellml")
#     return baseline_units

# def load_opb_mappings(rdf_file_path="rdf_unit_cellml.ttl"):
#     """
#     Load OPB ontology mappings from local RDF/Turtle file
#     Handle multiple OPB mappings per unit
#     """
#     opb_mappings = {}
    
#     if not os.path.exists(rdf_file_path):
#         raise Exception(f"RDF file {rdf_file_path} not found")
    
#     try:
#         with open(rdf_file_path, 'r', encoding='utf-8') as f:
#             content = f.read()
        
#         print(f"Parsing RDF file: {rdf_file_path}")
        
#         # Parse the RDF content - specific to your format
#         lines = content.split('\n')
#         current_unit = None
        
#         for line in lines:
#             line = line.strip()
            
#             # Skip empty lines and prefix declarations
#             if not line or line.startswith('@prefix'):
#                 continue
            
#             # Look for lines with is_unit_of:
#             if 'is_unit_of:' in line:
#                 # Format: ex:C_per_m2 is_unit_of: opb:OPB_01238 .
#                 parts = [p for p in line.split() if p]  # Remove empty parts
                
#                 if len(parts) >= 3:
#                     # Extract unit name from ex: prefix (remove "ex:")
#                     unit_part = parts[0]
#                     if ':' in unit_part:
#                         unit_name = unit_part.split(':')[1]
#                     else:
#                         unit_name = unit_part
                    
#                     current_unit = unit_name
#                     if unit_name not in opb_mappings:
#                         opb_mappings[unit_name] = []
                    
#                     # Extract OPB codes from the rest of the line
#                     for part in parts[2:]:
#                         if 'opb:OPB_' in part:
#                             opb_code = part.replace('opb:OPB_', 'OPB_').rstrip('.,;')
#                             opb_mappings[unit_name].append(opb_code)
#                             print(f"  Loaded mapping: {unit_name} → {opb_code}")
            
#             # Handle multi-line OPB mappings (lines that continue with more OPB codes)
#             elif current_unit and 'opb:OPB_' in line:
#                 # Continuation line with more OPB codes
#                 for part in line.split():
#                     if 'opb:OPB_' in part:
#                         opb_code = part.replace('opb:OPB_', 'OPB_').rstrip('.,;')
#                         opb_mappings[current_unit].append(opb_code)
#                         print(f"  Loaded mapping: {current_unit} → {opb_code}")
        
#         print(f"Successfully loaded {sum(len(v) for v in opb_mappings.values())} OPB mappings for {len(opb_mappings)} units")
#         return opb_mappings
        
#     except Exception as e:
#         print(f"Error parsing RDF file: {e}")
#         import traceback
#         traceback.print_exc()
#         raise

# def are_units_compatible(unit1, unit2):
#     """
#     Check if two units are compatible, but be more strict about dimensional analysis
#     """
#     try:
#         # First check basic compatibility
#         if not libcellml.Units.compatible(unit1, unit2):
#             return False
        
#         # Additional checks to prevent false positives
#         # For example, rad_per_s (radian/second) should not be compatible with per_millisecond (1/second)
        
#         # Get the dimensional composition of both units
#         dim1 = get_unit_dimensions(unit1)
#         dim2 = get_unit_dimensions(unit2)
        
#         # Units are only compatible if they have exactly the same dimensions
#         return dim1 == dim2
        
#     except Exception as e:
#         print(f"Error in compatibility check: {e}")
#         return False

# def get_unit_dimensions(units):
#     """
#     Get a dimensional signature for a unit to ensure proper compatibility checking
#     Returns a tuple representing the dimensional composition
#     """
#     dimensions = []
#     for i in range(units.unitCount()):
#         try:
#             ref = units.unitAttributeReference(i)
#             exponent = units.unitAttributeExponent(i)
#             dimensions.append((ref, exponent))
#         except:
#             continue
    
#     # Sort for consistent comparison
#     return tuple(sorted(dimensions))

# def map_units_to_opb(target_file_path):
#     """Map units from target CellML file to OPB ontology using baseline units"""
#     print(f"Mapping units for: {os.path.basename(target_file_path)}")
#     print("=" * 60)
    
#     try:
#         # Load baseline units and OPB mappings
#         baseline_units = parse_baseline_units()
#         opb_mappings = load_opb_mappings()
        
#         # Parse target model
#         with open(target_file_path, 'r', encoding='utf-8') as file:
#             target_content = file.read()
        
#         parser = libcellml.Parser()
#         parser.setStrict(False)
#         target_model = parser.parseModel(target_content)
        
#         if not target_model:
#             print("Failed to parse target model")
#             return
        
#         print(f"Target model: {target_model.name()}")
#         print(f"Units in target model: {target_model.unitsCount()}")
        
#         # Map each unit in target model to OPB via baseline units
#         unit_mappings = []
        
#         for i in range(target_model.unitsCount()):
#             target_unit = target_model.units(i)
#             target_unit_name = target_unit.name()
            
#             print(f"\nProcessing target unit: {target_unit_name}")
            
#             # Find compatible baseline units with strict checking
#             compatible_baseline_units = []
#             for baseline_name, baseline_unit in baseline_units.items():
#                 print(f"  🔄 Checking against baseline: {baseline_name}")
                
#                 # Use recursive compatibility checking
#                 if are_units_compatible(target_unit, baseline_unit):
#                     mapped = True
#                     baseline_id = get_unit_cmeta_id(baseline_unit)
                    
#                     # Check if we have OPB mapping for this baseline unit
#                     if baseline_id in opb_mappings:
#                         compatible_baseline_units.append(baseline_name)
#                         print(f"  ✓ Compatible with baseline unit: {baseline_name}")
#                     else:
#                         print(f"  ⚠ No OPB mapping found for baseline unit: {baseline_name}")
#                 else:
#                     print(f"  ❌ NOT compatible with {baseline_name}")
            
#             # Map to OPB ontology (handle multiple mappings)
#             opb_terms = []
#             for baseline_name in compatible_baseline_units:
#                 if baseline_name in opb_mappings:
#                     for opb_term in opb_mappings[baseline_name]:
#                         opb_terms.append((baseline_name, opb_term))
#                         print(f"    → Maps to OPB: {opb_term}")
#                 else:
#                     print(f"    ⚠ No OPB mapping found for baseline unit: {baseline_name}")
            
#             unit_mappings.append({
#                 'target_unit': target_unit_name,
#                 'compatible_baseline_units': compatible_baseline_units,
#                 'opb_mappings': opb_terms
#             })
        
#         # Generate results
#         print("\n" + "=" * 60)
#         print("MAPPING RESULTS")
#         print("=" * 60)
        
#         save_mapping_results(unit_mappings, target_file_path, opb_mappings)
        
#         # Print summary
#         total_mapped = sum(1 for mapping in unit_mappings if mapping['opb_mappings'])
#         total_opb_terms = sum(len(mapping['opb_mappings']) for mapping in unit_mappings)
        
#         print(f"\nSummary for {os.path.basename(target_file_path)}:")
#         print(f"  Total units: {len(unit_mappings)}")
#         print(f"  Units with OPB mappings: {total_mapped}")
#         print(f"  Total OPB mappings: {total_opb_terms}")
#         print(f"  Units without OPB mappings: {len(unit_mappings) - total_mapped}")
        
#         return unit_mappings
        
#     except Exception as e:
#         print(f"Error in mapping process: {e}")
#         import traceback
#         traceback.print_exc()

# def save_mapping_results(unit_mappings, target_file_path, opb_mappings):
#     """Save mapping results to a file"""
#     filename = os.path.basename(target_file_path)
#     output_file = f"opb_mappings_{filename}.txt"
    
#     with open(output_file, 'w', encoding='utf-8') as f:
#         f.write(f"OPB ONTOLOGY MAPPINGS FOR: {filename}\n")
#         f.write("=" * 80 + "\n\n")
        
#         f.write("BASELINE UNITS TO OPB MAPPINGS REFERENCE:\n")
#         f.write("-" * 50 + "\n")
#         for unit_name, opb_codes in sorted(opb_mappings.items()):
#             f.write(f"{unit_name} → {', '.join([f'OPB.{code}' for code in opb_codes])}\n")
#         f.write("\n")
        
#         f.write("UNIT MAPPINGS:\n")
#         f.write("-" * 40 + "\n")
        
#         for mapping in unit_mappings:
#             f.write(f"\nTarget unit: {mapping['target_unit']}\n")
            
#             if mapping['compatible_baseline_units']:
#                 f.write("Compatible with baseline units: " + ", ".join(mapping['compatible_baseline_units']) + "\n")
#             else:
#                 f.write("No compatible baseline units found\n")
            
#             if mapping['opb_mappings']:
#                 f.write("OPB ontology mappings:\n")
#                 for baseline_name, opb_term in mapping['opb_mappings']:
#                     f.write(f"  {baseline_name} → OPB.{opb_term}\n")
#             else:
#                 f.write("No OPB ontology mapping found\n")
        
#         # Summary statistics
#         total_units = len(unit_mappings)
#         mapped_units = sum(1 for m in unit_mappings if m['opb_mappings'])
#         total_opb_terms = sum(len(m['opb_mappings']) for m in unit_mappings)
        
#         f.write(f"\n\nSUMMARY:\n")
#         f.write("-" * 40 + "\n")
#         f.write(f"Total units: {total_units}\n")
#         f.write(f"Units with OPB mappings: {mapped_units}\n")
#         f.write(f"Total OPB mappings: {total_opb_terms}\n")
#         f.write(f"Units without OPB mappings: {total_units - mapped_units}\n")
#         if total_units > 0:
#             f.write(f"Mapping success rate: {(mapped_units/total_units)*100:.1f}%\n")
    
#     print(f"Mapping results saved to: {output_file}")

# def main():
#     # Start with Hodgkin_Huxley_1952.cellml
#     target_file = "Hodgkin_Huxley_1952.cellml"
    
#     if not os.path.exists(target_file):
#         print(f"Error: Target file '{target_file}' not found!")
#         return
    
#     if not os.path.exists("rdf_unit_cellml.ttl"):
#         print("Error: RDF file 'rdf_unit_cellml.ttl' not found!")
#         print("Please make sure the RDF file is in the same directory as this script.")
#         return
    
#     print("=" * 80)
#     print("MAPPING CELLML UNITS TO OPB ONTOLOGY")
#     print("=" * 80)
#     print("Using:")
#     print(f"  - Target file: {target_file}")
#     print("  - Baseline units: from GitHub")
#     print("  - OPB mappings: from local rdf_unit_cellml.ttl")
#     print("=" * 80)
    
#     # Map units to OPB ontology
#     unit_mappings = map_units_to_opb(target_file)
    
#     if unit_mappings:
#         print("\nMapping process completed successfully!")
#         print("Check the generated output file for detailed results.")
#     else:
#         print("Mapping process failed.")

# if __name__ == "__main__":
#     main()

