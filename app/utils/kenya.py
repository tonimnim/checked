"""
Kenya counties and regions for tournament restrictions
"""

# All 47 Kenya counties
KENYA_COUNTIES = [
    "Baringo", "Bomet", "Bungoma", "Busia", "Elgeyo-Marakwet",
    "Embu", "Garissa", "Homa Bay", "Isiolo", "Kajiado",
    "Kakamega", "Kericho", "Kiambu", "Kilifi", "Kirinyaga",
    "Kisii", "Kisumu", "Kitui", "Kwale", "Laikipia",
    "Lamu", "Machakos", "Makueni", "Mandera", "Marsabit",
    "Meru", "Migori", "Mombasa", "Murang'a", "Nairobi",
    "Nakuru", "Nandi", "Narok", "Nyamira", "Nyandarua",
    "Nyeri", "Samburu", "Siaya", "Taita-Taveta", "Tana River",
    "Tharaka-Nithi", "Trans-Nzoia", "Turkana", "Uasin Gishu",
    "Vihiga", "Wajir", "West Pokot"
]

# Regional groupings for convenience
KENYA_REGIONS = {
    "Nairobi": ["Nairobi"],
    "Coast": ["Mombasa", "Kilifi", "Kwale", "Taita-Taveta", "Lamu", "Tana River"],
    "Central": ["Kiambu", "Murang'a", "Nyeri", "Kirinyaga", "Nyandarua"],
    "Eastern": ["Embu", "Meru", "Tharaka-Nithi", "Kitui", "Machakos", "Makueni", "Isiolo", "Marsabit"],
    "Western": ["Kakamega", "Bungoma", "Busia", "Vihiga"],
    "Nyanza": ["Kisumu", "Siaya", "Homa Bay", "Migori", "Kisii", "Nyamira"],
    "Rift Valley": [
        "Nakuru", "Narok", "Kajiado", "Kericho", "Bomet", "Nandi",
        "Uasin Gishu", "Trans-Nzoia", "Elgeyo-Marakwet", "Baringo",
        "Laikipia", "Samburu", "West Pokot", "Turkana"
    ],
    "North Eastern": ["Garissa", "Wajir", "Mandera"]
}


def get_counties_by_region(region: str) -> list:
    """Get list of counties in a region"""
    return KENYA_REGIONS.get(region, [])


def expand_county_restrictions(counties: list) -> list:
    """
    Expand county list by including region names.
    If 'Coast' is in the list, expand to all Coast counties.
    """
    expanded = set()
    for item in counties:
        if item in KENYA_REGIONS:
            # It's a region, expand to counties
            expanded.update(KENYA_REGIONS[item])
        else:
            # It's a county
            expanded.add(item)
    return list(expanded)
