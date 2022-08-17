import math
import operator
import warnings
from typing import List, Optional, Tuple, Union

import jax
import jax.numpy as jnp
import jax.scipy
import numpy as np

import e3nn_jax as e3nn
from e3nn_jax import Irreps, axis_angle_to_angles, config, matrix_to_angles, quaternion_to_angles


class IrrepsArray:
    r"""Class storing an array and its irreps

    Args:
        irreps (`Irreps`): Irreps of the array
        array (``jnp.ndarray``): Array of shape ``(..., irreps.dim)``
        list (optional ``List[jnp.ndarray]``): List of arrays of shape ``(..., mul, ir.dim)``
    """

    irreps: Irreps
    array: jnp.ndarray  # this field is mendatory because it contains the shape
    _list: List[Optional[jnp.ndarray]]  # this field is lazy, it is computed only when needed

    def __init__(
        self, irreps: Irreps, array: jnp.ndarray, list: List[Optional[jnp.ndarray]] = None, _perform_checks: bool = True
    ):
        self.irreps = Irreps(irreps)
        self.array = array
        self._list = list

        if _perform_checks:
            if self.array.shape[-1] != self.irreps.dim:
                raise ValueError(
                    f"IrrepsArray: Array shape {self.array.shape} incompatible with irreps {self.irreps}. "
                    f"{self.array.shape[-1]} != {self.irreps.dim}"
                )
            if self._list is not None:
                if len(self._list) != len(self.irreps):
                    raise ValueError(f"IrrepsArray: List length {len(self._list)} incompatible with irreps {self.irreps}.")
                for x, (mul, ir) in zip(self._list, self.irreps):
                    if x is not None:
                        if x.shape != self.array.shape[:-1] + (mul, ir.dim):
                            raise ValueError(
                                f"IrrepsArray: List shapes {[None if x is None else x.shape for x in self._list]} "
                                f"incompatible with array shape {self.array.shape} and irreps {self.irreps}. "
                                f"Expecting {[self.array.shape[:-1] + (mul, ir.dim) for (mul, ir) in self.irreps]}."
                            )

    @staticmethod
    def from_any(irreps: Irreps, any) -> "IrrepsArray":
        r"""Create a new IrrepsArray

        Args:
            irreps (`Irreps`): the irreps of the data
            any: the data

        Returns:
            `IrrepsArray`
        """
        if isinstance(any, IrrepsArray):
            return any.convert(irreps)
        if isinstance(any, list):
            leading_shape = None
            for x in any:
                if x is not None:
                    leading_shape = x.shape[:-2]
            if leading_shape is None:
                raise ValueError("IrrepsArray.from_any cannot infer shape from list of arrays")
            return IrrepsArray.from_list(irreps, any, leading_shape)
        return IrrepsArray(irreps, any)

    @staticmethod
    def from_list(irreps: Irreps, list, leading_shape: Tuple[int]) -> "IrrepsArray":
        r"""Create an IrrepsArray from a list of arrays

        Args:
            irreps (Irreps): irreps
            list (list of optional ``jnp.ndarray``): list of arrays
            leading_shape (tuple of int): leading shape of the arrays (without the irreps)

        Returns:
            IrrepsArray
        """
        irreps = Irreps(irreps)
        if len(irreps) != len(list):
            raise ValueError(f"IrrepsArray.from_list: len(irreps) != len(list), {len(irreps)} != {len(list)}")

        if not all(x is None or isinstance(x, jnp.ndarray) for x in list):
            raise ValueError(f"IrrepsArray.from_list: list contains non-array elements type={[type(x) for x in list]}")

        if not all(x is None or x.shape == leading_shape + (mul, ir.dim) for x, (mul, ir) in zip(list, irreps)):
            raise ValueError(
                f"IrrepsArray.from_list: list shapes {[None if x is None else x.shape for x in list]} "
                f"incompatible with leading shape {leading_shape} and irreps {irreps}. "
                f"Expecting {[leading_shape + (mul, ir.dim) for (mul, ir) in irreps]}."
            )

        if irreps.dim > 0:
            array = jnp.concatenate(
                [
                    jnp.zeros(leading_shape + (mul_ir.dim,)) if x is None else x.reshape(leading_shape + (mul_ir.dim,))
                    for mul_ir, x in zip(irreps, list)
                ],
                axis=-1,
            )
        else:
            array = jnp.zeros(leading_shape + (0,))
        return IrrepsArray(irreps=irreps, array=array, list=list)

    @property
    def list(self) -> List[Optional[jnp.ndarray]]:
        if self._list is None:
            leading_shape = self.array.shape[:-1]
            if len(self.irreps) == 1:
                mul, ir = self.irreps[0]
                list = [jnp.reshape(self.array, leading_shape + (mul, ir.dim))]
            else:
                list = [
                    jnp.reshape(self.array[..., i], leading_shape + (mul, ir.dim))
                    for i, (mul, ir) in zip(self.irreps.slices(), self.irreps)
                ]
            self._list = list
        return self._list

    # def __jax_array__(self):
    #     if self.irreps.lmax > 0:
    #         return NotImplemented
    #     return self.array
    #
    # Note: - __jax_array__ seems to be incompatible with register_pytree_node
    #       - __jax_array__ cause problem for the multiplication: jnp.array * IrrepsArray -> jnp.array

    @staticmethod
    def zeros(irreps: Irreps, leading_shape) -> "IrrepsArray":
        irreps = Irreps(irreps)
        return IrrepsArray(irreps=irreps, array=jnp.zeros(leading_shape + (irreps.dim,)), list=[None] * len(irreps))

    @staticmethod
    def ones(irreps: Irreps, leading_shape) -> "IrrepsArray":
        irreps = Irreps(irreps)
        return IrrepsArray(
            irreps=irreps,
            array=jnp.ones(leading_shape + (irreps.dim,)),
            list=[jnp.ones(leading_shape + (mul, ir.dim)) for mul, ir in irreps],
        )

    def __repr__(self):
        r = str(self.array)
        if "\n" in r:
            return f"{self.irreps}\n{r}"
        return f"{self.irreps} {r}"

    @property
    def shape(self):
        return self.array.shape

    def __len__(self):
        return len(self.array)

    @property
    def ndim(self):
        return len(self.shape)

    def reshape(self, shape) -> "IrrepsArray":
        assert shape[-1] == self.irreps.dim or shape[-1] == -1
        shape = shape[:-1]
        list = [None if x is None else x.reshape(shape + (mul, ir.dim)) for (mul, ir), x in zip(self.irreps, self.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array.reshape(shape + (self.irreps.dim,)), list=list)

    def broadcast_to(self, shape) -> "IrrepsArray":
        assert isinstance(shape, tuple)
        assert shape[-1] == self.irreps.dim or shape[-1] == -1
        leading_shape = shape[:-1]
        array = jnp.broadcast_to(self.array, leading_shape + (self.irreps.dim,))
        list = [
            None if x is None else jnp.broadcast_to(x, leading_shape + (mul, ir.dim))
            for (mul, ir), x in zip(self.irreps, self.list)
        ]
        return IrrepsArray(irreps=self.irreps, array=array, list=list)

    def replace_none_with_zeros(self) -> "IrrepsArray":
        list = [jnp.zeros(self.shape[:-1] + (mul, ir.dim)) if x is None else x for (mul, ir), x in zip(self.irreps, self.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array, list=list)

    def remove_nones(self) -> "IrrepsArray":
        if any(x is None for x in self.list):
            irreps = [mul_ir for mul_ir, x in zip(self.irreps, self.list) if x is not None]
            list = [x for x in self.list if x is not None]
            return IrrepsArray.from_list(irreps, list, self.shape[:-1])
        return self

    def simplify(self) -> "IrrepsArray":
        return self.convert(self.irreps.simplify())

    def sorted(self) -> "IrrepsArray":
        irreps, p, inv = self.irreps.sort()
        return IrrepsArray.from_list(irreps, [self.list[i] for i in inv], self.shape[:-1])

    def split(self, indices: Union[List[int], List[Irreps]]) -> List["IrrepsArray"]:
        """Split the array into subarrays

        Examples:
            >>> IrrepsArray("0e + 1e", jnp.array([1.0, 2, 3, 4])).split(["0e", "1e"])
            [1x0e [1.], 1x1e [2. 3. 4.]]

            >>> IrrepsArray("0e + 1e", jnp.array([1.0, 2, 3, 4])).split([1])
            [1x0e [1.], 1x1e [2. 3. 4.]]
        """
        if all(isinstance(i, int) for i in indices):
            array_parts = jnp.split(self.array, [self.irreps[:i].dim for i in indices], axis=-1)
            assert len(array_parts) == len(indices) + 1
            return [
                IrrepsArray(irreps=self.irreps[i:j], array=array, list=self.list[i:j])
                for (i, j), array in zip(zip([0] + indices, indices + [len(self.irreps)]), array_parts)
            ]

        irrepss = [Irreps(i) for i in indices]
        assert self.irreps.simplify() == sum(irrepss, Irreps()).simplify()
        array_parts = jnp.split(
            self.array, [sum(irreps.dim for irreps in irrepss[:i]) for i in range(1, len(irrepss))], axis=-1
        )
        assert len(array_parts) == len(irrepss)
        return [IrrepsArray(irreps, array) for irreps, array in zip(irrepss, array_parts)]

    def __iter__(self):
        if self.ndim <= 1:
            raise ValueError("Can't iterate over IrrepsArray with ndim <= 1")
        for i in range(len(self)):
            yield self[i]

    def __getitem__(self, index) -> "IrrepsArray":
        if not isinstance(index, tuple):
            index = (index,)

        def is_ellipse(x):
            return type(x) == type(Ellipsis)

        def is_none_slice(x):
            return isinstance(x, slice) and x == slice(None)

        # Handle x[..., "1e"] and x[:, :, "1e"]
        if isinstance(index[-1], (Irreps, str)):
            if not (any(map(is_ellipse, index[:-1])) or len(index) == self.ndim):
                raise ValueError("Irreps index must be the last index")

            irreps = Irreps(index[-1])
            if len(irreps) != 1:
                raise ValueError('Only support indexing of a single "mul x ir"')
            if list(self.irreps).count(irreps[0]) != 1:
                raise ValueError(f"Can't slice with {irreps} because it doesn't appear exactly once in {self.irreps}")

            i = list(self.irreps).index(irreps[0])
            index = index[:-1] + (slice(None),)
            return IrrepsArray.from_list(irreps, self.list[i : i + 1], self.shape[:-1])[index]

        if len(index) == self.ndim or any(map(is_ellipse, index)):
            if not (is_ellipse(index[-1]) or is_none_slice(index[-1])):
                raise IndexError('Indexing of the last dimension of an IrrepsArray is restricted to "mul x ir"')
        return IrrepsArray(
            self.irreps,
            array=self.array[index],
            list=[None if x is None else x[index + (slice(None),)] for x in self.list],
        )

    def repeat_irreps_by_last_axis(self) -> "IrrepsArray":
        r"""Repeat the irreps by the last axis of the array.

        Example:
            >>> irreps = IrrepsArray("0e + 1e", jnp.zeros((3, 4)))
            >>> irreps.repeat_irreps_by_last_axis().irreps
            1x0e+1x1e+1x0e+1x1e+1x0e+1x1e
        """
        assert len(self.shape) >= 2
        irreps = (self.shape[-2] * self.irreps).simplify()
        array = self.array.reshape(self.shape[:-2] + (irreps.dim,))
        return IrrepsArray(irreps, array)

    def repeat_mul_by_last_axis(self) -> "IrrepsArray":
        r"""Repeat the multiplicity by the last axis of the array.

        Example:
            >>> irreps = IrrepsArray("0e + 1e", jnp.zeros((3, 4)))
            >>> irreps.repeat_mul_by_last_axis().irreps
            3x0e+3x1e
        """
        assert len(self.shape) >= 2
        irreps = Irreps([(self.shape[-2] * mul, ir) for mul, ir in self.irreps])
        list = [None if x is None else x.reshape(self.shape[:-2] + (mul, ir.dim)) for (mul, ir), x in zip(irreps, self.list)]
        return IrrepsArray.from_list(irreps, list, self.shape[:-2])

    def factor_irreps_to_last_axis(self) -> "IrrepsArray":
        raise NotImplementedError

    def factor_mul_to_last_axis(self, factor=None) -> "IrrepsArray":
        r"""Create a new axis in the previous last position by factoring the multiplicities.

        Example:
            >>> irreps = IrrepsArray("6x0e + 3x1e", jnp.zeros((3, 6 + 9)))
            >>> irreps.factor_mul_to_last_axis().irreps
            2x0e+1x1e
        """
        if factor is None:
            factor = math.gcd(*(mul for mul, _ in self.irreps))

        if not all(mul % factor == 0 for mul, _ in self.irreps):
            raise ValueError(f"factor {factor} does not divide all multiplicities")

        irreps = Irreps([(mul // factor, ir) for mul, ir in self.irreps])
        list = [
            None if x is None else x.reshape(self.shape[:-1] + (factor, mul, ir.dim))
            for (mul, ir), x in zip(irreps, self.list)
        ]
        return IrrepsArray.from_list(irreps, list, self.shape[:-1] + (factor,))

    def transform_by_angles(self, alpha: float, beta: float, gamma: float, k: int = 0) -> "IrrepsArray":
        r"""Rotate the data by angles according to the irreps

        Args:
            alpha (float): third rotation angle around the second axis (in radians)
            beta (float): second rotation angle around the first axis (in radians)
            gamma (float): first rotation angle around the second axis (in radians)
            k (int): parity operation

        Returns:
            IrrepsArray
        """
        # Optimization: we use only the list of arrays, not the array data
        D = {ir: ir.D_from_angles(alpha, beta, gamma, k) for ir in {ir for _, ir in self.irreps}}
        new_list = [
            jnp.reshape(jnp.einsum("ij,...uj->...ui", D[ir], x), self.shape[:-1] + (mul, ir.dim)) if x is not None else None
            for (mul, ir), x in zip(self.irreps, self.list)
        ]
        return IrrepsArray.from_list(self.irreps, new_list, self.shape[:-1])

    def transform_by_quaternion(self, q: jnp.ndarray, k: int = 0) -> "IrrepsArray":
        r"""Rotate data by a rotation given by a quaternion

        Args:
            q (``jnp.ndarray``): quaternion
            k (int): parity operation

        Returns:
            IrrepsArray
        """
        return self.transform_by_angles(*quaternion_to_angles(q), k)

    def transform_by_axis_angle(self, axis: jnp.ndarray, angle: float, k: int = 0) -> "IrrepsArray":
        r"""Rotate data by a rotation given by an axis and an angle

        Args:
            axis (``jnp.ndarray``): axis
            angle (float): angle (in radians)
            k (int): parity operation

        Returns:
            IrrepsArray
        """
        return self.transform_by_angles(*axis_angle_to_angles(axis, angle), k)

    def transform_by_matrix(self, R: jnp.ndarray) -> "IrrepsArray":
        r"""Rotate data by a rotation given by a matrix

        Args:
            R (``jnp.ndarray``): rotation matrix

        Returns:
            IrrepsArray
        """
        d = jnp.sign(jnp.linalg.det(R))
        R = d[..., None, None] * R
        k = (1 - d) / 2
        return self.transform_by_angles(*matrix_to_angles(R), k)

    def convert(self, irreps: Irreps) -> "IrrepsArray":
        r"""Convert the list property into an equivalent irreps

        Args:
            irreps (Irreps): new irreps

        Returns:
            `IrrepsArray`: data with the new irreps

        Raises:
            ValueError: if the irreps are not compatible

        Example:
        >>> id = IrrepsArray.from_any("10x0e + 10x0e", [None, jnp.ones((1, 10, 1))])
        >>> jax.tree_util.tree_map(lambda x: x.shape, id.convert("20x0e")).list
        [(1, 20, 1)]
        """
        # Optimization: we use only the list of arrays, not the array data
        irreps = Irreps(irreps)
        assert self.irreps.simplify() == irreps.simplify(), (self.irreps, irreps)
        # TODO test cases with mul == 0

        leading_shape = self.shape[:-1]

        new_list = []
        current_array = 0

        while len(new_list) < len(irreps) and irreps[len(new_list)].mul == 0:
            new_list.append(None)

        for mul_ir, y in zip(self.irreps, self.list):
            mul, _ = mul_ir

            while mul > 0:
                if isinstance(current_array, int):
                    current_mul = current_array
                else:
                    current_mul = current_array.shape[-2]

                needed_mul = irreps[len(new_list)].mul - current_mul

                if mul <= needed_mul:
                    x = y
                    m = mul
                    mul = 0
                elif mul > needed_mul:
                    if y is None:
                        x = None
                    else:
                        x, y = jnp.split(y, [needed_mul], axis=-2)
                    m = needed_mul
                    mul -= needed_mul

                if x is None:
                    if isinstance(current_array, int):
                        current_array += m
                    else:
                        current_array = jnp.concatenate(
                            [current_array, jnp.zeros(leading_shape + (m, mul_ir.ir.dim))], axis=-2
                        )
                else:
                    if isinstance(current_array, int):
                        if current_array == 0:
                            current_array = x
                        else:
                            current_array = jnp.concatenate(
                                [jnp.zeros(leading_shape + (current_array, mul_ir.ir.dim)), x], axis=-2
                            )
                    else:
                        current_array = jnp.concatenate([current_array, x], axis=-2)

                if isinstance(current_array, int):
                    if current_array == irreps[len(new_list)].mul:
                        new_list.append(None)
                        current_array = 0
                else:
                    if current_array.shape[-2] == irreps[len(new_list)].mul:
                        new_list.append(current_array)
                        current_array = 0

                while len(new_list) < len(irreps) and irreps[len(new_list)].mul == 0:
                    new_list.append(None)

        assert current_array == 0

        assert len(new_list) == len(irreps)
        assert all(x is None or isinstance(x, (jnp.ndarray, np.ndarray)) for x in new_list), [type(x) for x in new_list]
        assert all(x is None or x.shape[-2:] == (mul, ir.dim) for x, (mul, ir) in zip(new_list, irreps))

        return IrrepsArray(irreps=irreps, array=self.array, list=new_list)

    def __eq__(self, other) -> "IrrepsArray":
        if isinstance(other, IrrepsArray):
            if self.irreps != other.irreps:
                raise ValueError("IrrepsArray({self.irreps}) == IrrepsArray({other.irreps}) is not equivariant.")

            leading_shape = jnp.broadcast_shapes(self.shape[:-1], other.shape[:-1])

            def eq(mul: int, x: jnp.ndarray, y: jnp.ndarray) -> jnp.ndarray:
                if x is None and y is None:
                    return jnp.ones(leading_shape + (mul,), dtype="bool")
                if x is None:
                    x = 0.0
                if y is None:
                    y = 0.0

                return jnp.all(x == y, axis=-1)

            list = [eq(mul, x, y)[..., None] for (mul, ir), x, y in zip(self.irreps, self.list, other.list)]
            return IrrepsArray.from_list([(mul, "0e") for mul, _ in self.irreps], list, leading_shape)

        other = np.array(other)
        if self.irreps.lmax > 0 or (other.ndim > 0 and other.shape[-1] != 1):
            raise ValueError(f"IrrepsArray({self.irreps}) == scalar(shape={other.shape}) is not equivariant.")
        return IrrepsArray(irreps=self.irreps, array=self.array == other)

    def __add__(self, other) -> "IrrepsArray":
        if not isinstance(other, IrrepsArray):
            if all(ir == "0e" for _, ir in self.irreps):
                return IrrepsArray(irreps=self.irreps, array=self.array + other)
            raise ValueError(f"IrrepsArray({self.irreps}) + scalar is not equivariant.")

        if self.irreps != other.irreps:
            raise ValueError(f"IrrepsArray({self.irreps}) + IrrepsArray({other.irreps}) is not equivariant.")

        list = [x if y is None else (y if x is None else x + y) for x, y in zip(self.list, other.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array + other.array, list=list)

    def __sub__(self, other) -> "IrrepsArray":
        if not isinstance(other, IrrepsArray):
            if all(ir == "0e" for _, ir in self.irreps):
                return IrrepsArray(irreps=self.irreps, array=self.array - other)
            raise ValueError(f"IrrepsArray({self.irreps}) - scalar is not equivariant.")

        if self.irreps != other.irreps:
            raise ValueError(f"IrrepsArray({self.irreps}) - IrrepsArray({other.irreps}) is not equivariant.")
        list = [x if y is None else (-y if x is None else x - y) for x, y in zip(self.list, other.list)]
        return IrrepsArray(irreps=self.irreps, array=self.array - other.array, list=list)

    def __mul__(self, other) -> "IrrepsArray":
        if isinstance(other, IrrepsArray):
            if self.irreps.lmax > 0 and other.irreps.lmax > 0:
                raise ValueError(
                    "x * y with both x and y non scalar and ambiguous. Use e3nn.elementwise_tensor_product instead."
                )
            return e3nn.elementwise_tensor_product(self, other)

        other = jnp.array(other)
        if self.irreps.lmax > 0 and other.ndim > 0 and other.shape[-1] != 1:
            raise ValueError(f"IrrepsArray({self.irreps}) * scalar(shape={other.shape}) is not equivariant.")
        list = [None if x is None else x * other[..., None] for x in self.list]
        return IrrepsArray(irreps=self.irreps, array=self.array * other, list=list)

    def __rmul__(self, other) -> "IrrepsArray":
        return self * other

    def __truediv__(self, other) -> "IrrepsArray":
        if isinstance(other, IrrepsArray):
            if len(other.irreps) == 0 or other.irreps.lmax > 0 or self.irreps.num_irreps != other.irreps.num_irreps:
                raise ValueError(f"IrrepsArray({self.irreps}) / IrrepsArray({other.irreps}) is not equivariant.")

            if any(x is None for x in other.list):
                raise ValueError("There are deterministic Zeros in the array of the lhs. Cannot divide by Zero.")
            other = 1.0 / other
            return e3nn.elementwise_tensor_product(self, other)

        other = jnp.array(other)
        if self.irreps.lmax > 0 and other.ndim > 0 and other.shape[-1] != 1:
            raise ValueError(f"IrrepsArray({self.irreps}) / scalar(shape={other.shape}) is not equivariant.")
        list = [None if x is None else x / other[..., None] for x in self.list]
        return IrrepsArray(irreps=self.irreps, array=self.array / other, list=list)

    def __rtruediv__(self, other) -> "IrrepsArray":
        other = jnp.array(other)
        if self.irreps.lmax > 0:
            raise ValueError(f"scalar(shape={other.shape}) / IrrepsArray({self.irreps}) is not equivariant.")
        if any(x is None for x in self.list):
            raise ValueError("There are deterministic Zeros in the array of the lhs. Cannot divide by Zero.")

        return IrrepsArray(irreps=self.irreps, array=other / self.array, list=[other[..., None] / x for x in self.list])

    @staticmethod
    def cat(args, axis=-1) -> "IrrepsArray":
        warnings.warn("IrrepsArray.cat is deprecated, use e3nn.concatenate instead", DeprecationWarning)
        return concatenate(args, axis=axis)

    @staticmethod
    def randn(irreps, key, leading_shape=(), *, normalization=None):
        warnings.warn("IrrepsArray.randn is deprecated, use e3nn.normal instead", DeprecationWarning)
        return normal(irreps, key, leading_shape=leading_shape, normalization=normalization)


jax.tree_util.register_pytree_node(
    IrrepsArray,
    lambda x: ((x.array, x.list), x.irreps),
    lambda x, data: IrrepsArray(irreps=x, array=data[0], list=data[1], _perform_checks=False),
)


def _standardize_axis(axis, ndim):
    if axis is None:
        return tuple(range(ndim))
    try:
        axis = (operator.index(axis),)
    except TypeError:
        axis = tuple(operator.index(i) for i in axis)

    assert all(-ndim <= i < ndim for i in axis)
    axis = tuple(i % ndim for i in axis)

    return tuple(sorted(set(axis)))


def _reduce(op, array: IrrepsArray, axis=None, keepdims=False):
    axis = _standardize_axis(axis, array.ndim)

    if axis == ():
        return array

    if axis[-1] < array.ndim - 1:
        # irrep dimension is not affected by mean
        return IrrepsArray(
            array.irreps,
            op(array.array, axis=axis, keepdims=keepdims),
            [None if x is None else op(x, axis=axis, keepdims=keepdims) for x in array.list],
        )

    array = _reduce(op, array, axis=axis[:-1], keepdims=keepdims)
    return IrrepsArray.from_list(
        Irreps([(1, ir) for _, ir in array.irreps]),
        [None if x is None else op(x, axis=-2, keepdims=True) for x in array.list],
        array.shape[:-1],
    )


def mean(array: IrrepsArray, axis=None, keepdims=False) -> IrrepsArray:
    """Mean of IrrepsArray along the specified axis."""
    return _reduce(jnp.mean, array, axis, keepdims)


def sum_(array: IrrepsArray, axis=None, keepdims=False) -> IrrepsArray:
    """Sum of IrrepsArray along the specified axis."""
    return _reduce(jnp.sum, array, axis, keepdims)


def concatenate(arrays, axis=-1) -> "IrrepsArray":
    r"""Concatenate a list of IrrepsArray

    Args:
        arrays (list of `IrrepsArray`): list of data to concatenate
        axis (int): axis to concatenate on

    Returns:
        concatenated `IrrepsArray`
    """
    assert len(arrays) >= 1
    assert isinstance(axis, int)
    assert -arrays[0].ndim <= axis < arrays[0].ndim

    axis = axis % arrays[0].ndim

    if axis == arrays[0].ndim - 1:
        irreps = Irreps(sum([x.irreps for x in arrays], Irreps("")))
        return IrrepsArray(
            irreps=irreps,
            array=jnp.concatenate([x.array for x in arrays], axis=-1),
            list=sum([x.list for x in arrays], []),
        )

    assert {x.irreps for x in arrays} == {arrays[0].irreps}
    arrays = [x.replace_none_with_zeros() for x in arrays]  # TODO this could be optimized
    return IrrepsArray(
        irreps=arrays[0].irreps,
        array=jnp.concatenate([x.array for x in arrays], axis=axis),
        list=[jnp.concatenate(xs, axis=axis) for xs in zip(*[x.list for x in arrays])],
    )


def norm(array: IrrepsArray, *, squared=False) -> IrrepsArray:
    """Norm of IrrepsArray."""

    def f(x):
        x = jnp.sum(x**2, axis=-1, keepdims=True)
        if not squared:
            x = jnp.sqrt(x)
        return x

    return IrrepsArray.from_list(
        [(mul, "0e") for mul, _ in array.irreps],
        [f(x) for x in array.list],
        array.shape[:-1],
    )


def normal(
    irreps: Irreps, key: jnp.ndarray, leading_shape: Tuple[int, ...] = (), *, normalization: bool = None
) -> IrrepsArray:
    r"""Random array with normal distribution."""
    irreps = Irreps(irreps)

    if normalization is None:
        normalization = config("irrep_normalization")

    if normalization == "component":
        return IrrepsArray(irreps, jax.random.normal(key, leading_shape + (irreps.dim,)))
    elif normalization == "norm":
        list = []
        for mul, ir in irreps:
            key, k = jax.random.split(key)
            r = jax.random.normal(k, leading_shape + (mul, ir.dim))
            r = r / jnp.linalg.norm(r, axis=-1, keepdims=True)
            list.append(r)
        return IrrepsArray.from_list(irreps, list, leading_shape)
    else:
        raise ValueError("Normalization needs to be 'norm' or 'component'")
