from django import forms

from .models import Product, Category


class ProductForm(forms.ModelForm):

    class Meta:
        model = Product

        fields = [
            "name",
            "category",
            "selling_price",
            "cost_price",
            "stock",
            "low_stock_threshold",
            "barcode",
            "active",
        ]

        widgets = {
            "name": forms.TextInput(
                attrs={
                    "placeholder": "Enter product name",
                }
            ),

            "selling_price": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "Enter selling price",
                }
            ),

            "cost_price": forms.NumberInput(
                attrs={
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "Enter cost price",
                }
            ),

            "stock": forms.NumberInput(
                attrs={
                    "min": "0",
                }
            ),

            "low_stock_threshold": forms.NumberInput(
                attrs={
                    "min": "0",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        """
        Receive the current store from the view and show
        only categories belonging to that store.
        """

        self.store = kwargs.pop("store", None)

        super().__init__(*args, **kwargs)

        if self.store:
            self.fields["category"].queryset = (
                Category.objects
                .filter(store=self.store)
                .order_by("name")
            )
        else:
            self.fields["category"].queryset = Category.objects.none()

    def clean_category(self):
        """
        Prevent a user from submitting a category belonging
        to another store, even if they manually manipulate
        the form request.
        """

        category = self.cleaned_data.get("category")

        if category is None:
            return category

        if not self.store:
            raise forms.ValidationError(
                "No store is assigned to your account."
            )

        if category.store_id != self.store.id:
            raise forms.ValidationError(
                "This category does not belong to your store."
            )

        return category

    def clean_selling_price(self):
        price = self.cleaned_data["selling_price"]

        if price < 0:
            raise forms.ValidationError(
                "Selling price cannot be negative."
            )

        return price

    def clean_cost_price(self):
        price = self.cleaned_data.get("cost_price")

        if price is not None and price < 0:
            raise forms.ValidationError(
                "Cost price cannot be negative."
            )

        return price
